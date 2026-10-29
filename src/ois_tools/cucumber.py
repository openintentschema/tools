"""Gherkin scenarios as criteria, Cucumber Messages as evidence. A scenario worded exactly as its
approved criterion counts as derived from the intent; ois cucumber records that it checked this."""

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

from gherkin.errors import CompositeParserException, ParserError
from gherkin.parser import Parser
from gherkin.token_scanner import TokenScanner

from ois_tools import records
from ois_tools.records import KEY_PATTERN, Recorded

Record = dict[str, Any]

ERRORS = {"UNDEFINED", "AMBIGUOUS", "UNKNOWN"}
SKIPS = {"SKIPPED", "PENDING"}


class CucumberError(ValueError):
    """The features or the run cannot be turned into records."""


def statement(scenario: Record, background: Record | None = None) -> str:
    """The criterion statement of a scenario: its background and own steps, then its examples."""
    lines = []
    for step in [*(background or {}).get("steps", []), *scenario.get("steps", [])]:
        lines.append(f"{step['keyword'].strip()} {step['text']}")
        if "dataTable" in step:
            lines.extend(_row(row) for row in step["dataTable"]["rows"])
        if "docString" in step:
            lines.extend(['"""', *step["docString"]["content"].splitlines(), '"""'])
    for examples in scenario.get("examples", []):
        lines.append(f"Examples: {examples['name']}" if examples.get("name") else "Examples:")
        header = examples.get("tableHeader")
        lines.extend(_row(row) for row in ([header] if header else []) + examples["tableBody"])
    return "\n".join(lines)


def key_of(scenario: Record, pattern: str = KEY_PATTERN) -> str | None:
    keys = [
        match[1]
        for tag in scenario.get("tags", [])
        if (match := re.fullmatch(pattern, tag["name"].lstrip("@")))
    ]
    if len(keys) > 1:
        raise CucumberError(f"scenario {scenario['name']!r} has several key tags: {keys}")
    return keys[0] if keys else None


def draft_intent(
    features: Iterable[str],
    *,
    id: str,
    created_at: datetime,
    title: str | None = None,
    produced_by: str | None = None,
    key_pattern: str = KEY_PATTERN,
    skipped: list[str] | None = None,
) -> Record:
    """A draft intent (version 1) with a gherkin criterion per keyed scenario; scenarios without
    a key tag go to `skipped`."""
    criteria: list[Record] = []
    titles, languages = [], []
    for text in features:
        feature = _parse(text).get("feature")
        if not feature:
            continue
        titles.append(feature["name"])
        languages.append(feature.get("language", "en"))
        for scenario, background in _scenarios(feature):
            key = key_of(scenario, key_pattern)
            if key is None:
                if skipped is not None:
                    skipped.append(f"{feature['name']}: {scenario['name']} (no key tag)")
                continue
            if any(c["key"] == key for c in criteria):
                raise CucumberError(f"key {key} is used by two scenarios")
            criteria.append(
                {
                    "key": key,
                    "statement": statement(scenario, background),
                    "notation": "gherkin",
                    "kind": "functional",
                    "language": feature.get("language", "en"),
                }
            )
    if not criteria:
        raise CucumberError("no scenario has a key tag")
    if title is None and len(titles) != 1:
        raise CucumberError("several features: pass a title")
    language = languages[0] if len(set(languages)) == 1 else None
    if language is not None:
        for criterion in criteria:
            del criterion["language"]
    intent: Record = {
        "ois": "0.1",
        "type": "intent",
        "id": id,
        "createdAt": records._timestamp(created_at),
        "version": 1,
        "title": title or titles[0],
        "status": "draft",
    }
    if language is not None:
        intent["language"] = language
    if produced_by is not None:
        intent["producedBy"] = produced_by
    intent["criteria"] = criteria
    return intent


def result_of(statuses: Iterable[str]) -> str:
    """One result for a scenario from its step statuses (hooks included)."""
    found = set(statuses)
    if "FAILED" in found:
        return "fail"
    if found & ERRORS:
        return "error"
    if found & SKIPS:
        return "skipped"
    return "pass"


def record_run(
    messages: Iterable[str],
    *,
    intent: Record,
    test_producer: str,
    namespace: str,
    repo: Path,
    created_at: datetime,
    subject: str | None = None,
    run: str | None = None,
    location: str | None = None,
    key_pattern: str = KEY_PATTERN,
) -> Recorded:
    """The adapter actor, a test artifact per feature file and an evidence record per executed
    scenario that names a criterion of the approved `intent`."""
    if intent.get("status") != "approved":
        raise CucumberError("approve the intent first: evidence refers to an approved version")
    reference = f"{intent['id']}@{intent['version']}"
    criteria = {c["key"]: c for c in intent["criteria"]}
    run_data = _read(messages)
    adapter = records.actor(
        kind="tool",
        name="ois cucumber (Cucumber Messages adapter of ois-tools)",
        version=package_version("ois-tools"),
        created_at=created_at,
        id=f"{namespace.rstrip('/')}/actors/ois-cucumber",
    )
    adapter_ref = records.reference(adapter)

    recorded = Recorded([adapter], adapter_ref)
    results: dict[str, list[tuple[str, bool, str, datetime]]] = {}  # uri -> evidence to write
    verbatim: dict[str, list[str]] = {}  # uri -> keys worded as their criterion
    for execution in run_data:
        scenario, background, uri = execution.scenario, execution.background, execution.uri
        key = key_of(scenario, key_pattern)
        where = f"{uri}: {scenario['name']}"
        if key is None or key not in criteria:
            reason = "no key tag" if key is None else f"{key} is not a criterion of {reference}"
            if f"{where} ({reason})" not in recorded.unmatched:
                recorded.unmatched.append(f"{where} ({reason})")
            continue
        same = statement(scenario, background) == criteria[key]["statement"]
        if same and key not in verbatim.setdefault(uri, []):
            verbatim[uri].append(key)
        note = f"{key}: {where} differs from {reference}#{key}, so it is not derived from it"
        if not same and note not in recorded.notes:
            recorded.notes.append(note)
        results.setdefault(uri, []).append((key, same, execution.result, execution.finished))

    order = list(criteria)
    for uri, executions in results.items():
        context = [f"{reference}#{key}" for key in order if key in verbatim.get(uri, [])]
        test = records.artifact(
            records.file_source(repo / uri, location),
            kind="test",
            produced_by=test_producer,
            created_at=created_at,
            namespace=namespace,
            context=context,
            context_recorded_by=adapter_ref if context else None,
        )
        recorded.records.append(test)
        for index, (key, same, result, finished) in enumerate(executions):
            anchor = hashlib.sha256(
                f"{test['id']}\n{key}\n{index}\n{finished}".encode()
            ).hexdigest()
            recorded.records.append(
                records.evidence(
                    id=f"{namespace.rstrip('/')}/evidence/{key}-{anchor[:12]}",
                    criterion=f"{reference}#{key}",
                    test=test["id"],
                    derived_from="intent" if same else "unknown",
                    result=result,
                    recorded_by=adapter_ref,
                    observed_at=finished,
                    created_at=created_at,
                    subject=subject,
                    run=run,
                )
            )
    return recorded


@dataclass
class _Execution:
    uri: str
    scenario: Record
    background: Record | None
    result: str
    finished: datetime


def _read(messages: Iterable[str]) -> list[_Execution]:
    """The final attempt of every executed scenario in a Cucumber Messages stream."""
    scenarios: dict[str, tuple[str, Record, Record | None]] = {}
    pickles: dict[str, Record] = {}
    cases: dict[str, str] = {}  # test case id -> pickle id
    started: dict[str, str] = {}  # test case started id -> test case id
    statuses: dict[str, list[str]] = {}
    finished: list[tuple[str, datetime]] = []
    for line in messages:
        if not line.strip():
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as error:
            raise CucumberError(f"not Cucumber Messages (NDJSON): {error}") from error
        if "gherkinDocument" in envelope:
            document = envelope["gherkinDocument"]
            for scenario, background in _scenarios(document.get("feature") or {}):
                scenarios[scenario["id"]] = (document["uri"], scenario, background)
        elif "pickle" in envelope:
            pickles[envelope["pickle"]["id"]] = envelope["pickle"]
        elif "testCase" in envelope:
            cases[envelope["testCase"]["id"]] = envelope["testCase"]["pickleId"]
        elif "testCaseStarted" in envelope:
            started[envelope["testCaseStarted"]["id"]] = envelope["testCaseStarted"]["testCaseId"]
        elif "testStepFinished" in envelope:
            step = envelope["testStepFinished"]
            statuses.setdefault(step["testCaseStartedId"], []).append(
                step["testStepResult"]["status"]
            )
        elif "testCaseFinished" in envelope:
            done = envelope["testCaseFinished"]
            if not done.get("willBeRetried"):
                finished.append((done["testCaseStartedId"], _time(done["timestamp"])))
    executions = []
    for started_id, moment in finished:
        pickle = pickles[cases[started[started_id]]]
        uri, scenario, background = scenarios[pickle["astNodeIds"][0]]
        result = result_of(statuses.get(started_id, []))
        executions.append(_Execution(uri, scenario, background, result, moment))
    if not executions:
        raise CucumberError("the messages hold no finished test case")
    return executions


def _scenarios(feature: Record) -> Iterator[tuple[Record, Record | None]]:
    """Each scenario of a feature with the background steps that run before it."""
    yield from _children(feature.get("children", []), [])


def _children(
    children: list[Record], inherited: list[Record]
) -> Iterator[tuple[Record, Record | None]]:
    steps = list(inherited)
    for child in children:
        if "background" in child:
            steps = [*steps, *child["background"]["steps"]]
        elif "scenario" in child:
            yield child["scenario"], ({"steps": steps} if steps else None)
        elif "rule" in child:
            yield from _children(child["rule"].get("children", []), steps)


def _parse(text: str) -> Record:
    try:
        document = Parser().parse(TokenScanner(text))
    except (CompositeParserException, ParserError) as error:
        raise CucumberError(f"not valid Gherkin: {error}") from error
    return dict(document)


def _row(row: Record) -> str:
    return "| " + " | ".join(cell["value"] for cell in row["cells"]) + " |"


def _time(timestamp: Record) -> datetime:
    return datetime.fromtimestamp(timestamp["seconds"] + timestamp.get("nanos", 0) / 1e9, UTC)
