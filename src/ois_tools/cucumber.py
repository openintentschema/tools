"""Gherkin scenarios as criteria, Cucumber runs as evidence. A scenario worded exactly as its
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
HOOKS = {"Before", "After"}  # cucumber-js reports hooks as steps
BEHAVE = {"failed": "FAILED", "skipped": "SKIPPED"}  # scenario statuses; others are errors
NOT_A_REPORT = "not Cucumber Messages, Cucumber JSON or behave JSON"


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
    report: str,
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
    context_recorded_by: str | None = None,
    reported_at: datetime | None = None,
) -> Recorded:
    """The adapter actor, a test artifact per feature file and an evidence record per executed
    scenario that names a criterion of the approved `intent`. `report` is Cucumber Messages
    (NDJSON), Cucumber JSON or behave JSON; JSON holds no times, so its evidence is observed at
    `reported_at` (default `created_at`). `context_recorded_by` names the actor that ran the check
    and signs, instead of the adapter."""
    if intent.get("status") != "approved":
        raise CucumberError("approve the intent first: evidence refers to an approved version")
    reference = f"{intent['id']}@{intent['version']}"
    criteria = {c["key"]: c for c in intent["criteria"]}
    run_data = _read(report, repo, reported_at or created_at)
    adapter = records.actor(
        kind="tool",
        name="ois cucumber (Cucumber adapter of ois-tools)",
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
            context_recorded_by=(context_recorded_by or adapter_ref) if context else None,
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


def _read(report: str, repo: Path, reported_at: datetime) -> list[_Execution]:
    if report.lstrip().startswith("["):
        try:
            features = json.loads(report)
        except json.JSONDecodeError as error:
            raise CucumberError(f"not Cucumber JSON: {error}") from error
        executions = _read_json(features, repo, reported_at)
    else:
        executions = _read_messages(report.splitlines(), repo)
    if not executions:
        raise CucumberError("the report holds no executed scenario")
    return executions


def _read_messages(messages: Iterable[str], repo: Path) -> list[_Execution]:
    """The final attempt of every executed scenario in a Cucumber Messages stream."""
    scenarios: dict[str, tuple[str, Record, Record | None]] = {}
    pickles: dict[str, Record] = {}
    cases: dict[str, str] = {}  # test case id -> pickle id
    started: dict[str, str] = {}  # test case started id -> test case id
    statuses: dict[str, list[str]] = {}
    finished: list[tuple[str, datetime]] = []
    cucumber = False  # a stream starts with its meta message
    for line in messages:
        if not line.strip():
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as error:
            raise CucumberError(f"{NOT_A_REPORT}: {error}") from error
        if not isinstance(envelope, dict):
            raise CucumberError(NOT_A_REPORT)
        cucumber = cucumber or "meta" in envelope
        if "gherkinDocument" in envelope:
            document = envelope["gherkinDocument"]
            uri = _feature_path(repo, document["uri"])
            for scenario, background in _scenarios(document.get("feature") or {}):
                scenarios[scenario["id"]] = (uri, scenario, background)
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
    if not cucumber:
        raise CucumberError(NOT_A_REPORT)
    executions = []
    for started_id, moment in finished:
        pickle = pickles[cases[started[started_id]]]
        uri, scenario, background = scenarios[pickle["astNodeIds"][0]]
        result = result_of(statuses.get(started_id, []))
        executions.append(_Execution(uri, scenario, background, result, moment))
    return executions


def _read_json(features: Any, repo: Path, reported_at: datetime) -> list[_Execution]:
    """Every executed scenario in Cucumber JSON or behave JSON. The scenarios are read from the
    feature files, by line: the report holds outlines with their values filled in."""
    if not isinstance(features, list) or not all(
        isinstance(f, dict) and "elements" in f and ("uri" in f or "location" in f)
        for f in features
    ):
        raise CucumberError(NOT_A_REPORT)
    executions = []
    for feature in features:
        uri = _feature_path(repo, feature.get("uri") or feature["location"].rpartition(":")[0])
        try:
            text = (repo / uri).read_text(encoding="utf-8")
        except OSError as error:
            raise CucumberError(f"{uri}: feature file not found in {repo}") from error
        lines = _by_line(_parse(text).get("feature") or {})
        background: list[str] = []  # cucumber-jvm reports the background before each scenario
        background_steps = 0
        for element in feature["elements"]:
            statuses, steps = _statuses(element)
            if element.get("type") == "background":
                background, background_steps = statuses, steps
                continue
            if not steps and element.get("status") in (None, "skipped", "untested"):
                continue  # not run, as deselected scenarios in behave
            line = element.get("line") or int(element["location"].rpartition(":")[2])
            if line not in lines:
                raise CucumberError(f"{uri}:{line} is no scenario; the run had another version")
            scenario, scenario_background = lines[line]
            statuses = [*background, *statuses]
            expected = len(scenario.get("steps", []))
            expected += len((scenario_background or {}).get("steps", []))
            if "status" not in element and background_steps + steps < expected:
                # pytest-bdd leaves out undefined steps; behave gives the scenario's status
                statuses.append("UNKNOWN")
            background, background_steps = [], 0
            result = result_of(statuses)
            executions.append(_Execution(uri, scenario, scenario_background, result, reported_at))
    return executions


def _statuses(element: Record) -> tuple[list[str], int]:
    """Step and hook statuses of a Cucumber JSON or behave JSON element, and the number of its
    steps with a result."""
    statuses = [_status(hook) for hook in [*element.get("before", []), *element.get("after", [])]]
    steps = 0
    for step in element.get("steps", []):
        statuses.extend(_status(hook) for hook in [*step.get("before", []), *step.get("after", [])])
        if "result" in step:
            statuses.append(_status(step))
            steps += step["keyword"].strip() not in HOOKS
    if element.get("status") not in (None, "passed"):  # behave
        statuses.append(BEHAVE.get(element["status"], "UNKNOWN"))
    return statuses, steps


def _status(item: Record) -> str:
    status = str(item.get("result", {}).get("status", "unknown")).upper()
    return status if status in {"PASSED", "FAILED", *ERRORS, *SKIPS} else "UNKNOWN"


def _by_line(feature: Record) -> dict[int, tuple[Record, Record | None]]:
    """Scenarios by the line of their keyword and of each example row."""
    lines = {}
    for scenario, background in _scenarios(feature):
        lines[scenario["location"]["line"]] = (scenario, background)
        for examples in scenario.get("examples", []):
            for row in examples.get("tableBody", []):
                lines[row["location"]["line"]] = (scenario, background)
    return lines


def _feature_path(repo: Path, uri: str) -> str:
    """The path of a feature file in `repo`; cucumber-jvm names it on the class path."""
    if not uri.startswith("classpath:"):
        return uri
    name = uri.removeprefix("classpath:").lstrip("/")
    try:
        tracked = records._git(repo, "ls-files").splitlines()
    except records.RecordError as error:
        raise CucumberError(f"{uri}: the feature files must be in Git: {error}") from error
    found = [path for path in tracked if path == name or path.endswith(f"/{name}")]
    if len(found) != 1:
        which = "no file" if not found else f"several files ({', '.join(found)})"
        raise CucumberError(f"{uri} matches {which} in Git")
    return found[0]


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
