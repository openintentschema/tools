"""JUnit XML to OIS: a Level 1 bundle (section 11.4), or evidence for the criteria of an approved
intent that tests name by key. The standard library's Expat refuses entity expansion and external
entities."""

import hashlib
import re
import subprocess
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from importlib.metadata import version as package_version
from pathlib import Path, PurePosixPath
from typing import Any

from ois_tools import records, rules
from ois_tools.document import Document, Entry
from ois_tools.records import KEY_PATTERN, Recorded, RecordError, location_of

Record = dict[str, Any]

RESULTS = ("pass", "skipped", "fail", "error")  # from best to worst
KEY_PROPERTY = "criterion"  # <property name="criterion" value="AC-1"/> on a test case


@dataclass(frozen=True)
class JUnitReport:
    content: bytes
    written_at: datetime | None = None  # file modification time, for timestamps without offset


@dataclass(frozen=True)
class Options:
    namespace: str  # URI under the producer's domain, e.g. https://ois.acme.example
    suite: str  # team or suite name; the test suite actor is named after it
    repo: Path  # Git working tree that holds the test files
    location: str | None = None  # defaults to git+<origin>@<HEAD>
    run: str | None = None  # link to the pipeline run
    now: datetime | None = None


@dataclass(frozen=True)
class TestCase:
    classname: str
    name: str
    file: str | None
    result: str
    observed_at: datetime
    report: str  # sha256 of the report the case comes from
    properties: tuple[tuple[str, str], ...] = ()


def to_bundle(reports: list[JUnitReport], options: Options) -> Record:
    """Build the bundle; the caller validates it before passing it on."""
    now = (options.now or datetime.now(UTC)).astimezone(UTC)
    ns = options.namespace.rstrip("/")
    commit = _git(options.repo, "rev-parse", "HEAD")
    location = options.location or location_of(options.repo)
    tracked = [path for path in _git(options.repo, "ls-files", "-z").split("\0") if path]
    cases = _dedupe([case for report in reports for case in _cases(report, now)])

    adapter = _record(
        "actor",
        f"{ns}/actors/ois-junit",
        now,
        kind="tool",
        name="ois junit (JUnit XML adapter of ois-tools)",
        version=package_version("ois-tools"),
    )
    suite = _record(
        "actor",
        f"{ns}/actors/{_slug(options.suite)}-tests",
        now,
        kind="tool",
        name=f"{options.suite} test suite",
        version="unknown",
    )
    adapter_ref = f"{adapter['id']}@{adapter['version']}"
    suite_ref = f"{suite['id']}@{suite['version']}"

    intent = _record(
        "intent",
        f"{ns}/intents/{_slug(options.suite)}-characterization",
        now,
        version=1,
        title=f"Characterization of the {options.suite} test suite",
        status="draft",
        producedBy=adapter_ref,
        criteria=[
            {"key": _key(c), "statement": _statement(c), "notation": "text", "kind": "functional"}
            for c in cases
        ],
    )

    artifacts: dict[str, Record] = {}
    evidence = []
    for case in cases:
        path = _test_file(tracked, case)
        artifact = _artifact(ns, options.repo, commit, location, path, case, suite_ref, now)
        artifacts.setdefault(artifact["id"], artifact)
        record = _record(
            "evidence",
            f"{ns}/evidence/{case.report[:12]}-{_key(case)}",
            now,
            criterion=f"{intent['id']}@1#{_key(case)}",
            test=artifact["id"],
            derivedFrom="unknown",
            result=case.result,
            observedAt=_timestamp(case.observed_at),
            recordedBy=adapter_ref,
        )
        if options.run:
            record["run"] = options.run
        evidence.append(record)

    combined = hashlib.sha256("".join(c.report for c in cases).encode()).hexdigest()
    records = [adapter, suite, intent, *artifacts.values(), *evidence]
    return _record("bundle", f"{ns}/bundles/junit-{combined[:16]}", now, records=records)


def keys_of(case: TestCase, pattern: str = KEY_PATTERN) -> list[str]:
    """Criterion keys of a test: its `criterion` properties, then keys in its name as whole
    words. Group 1 of the pattern, if it has one, is the key."""
    found = [
        key
        for name, value in case.properties
        if name == KEY_PROPERTY
        for key in re.split(r"[\s,]+", value)
        if key
    ]
    for match in re.finditer(rf"(?<![\w-])(?:{pattern})(?!\w)", case.name):
        found.append(match[1] if match.re.groups else match[0])
    return list(dict.fromkeys(found))


def record_run(
    reports: list[JUnitReport],
    *,
    intent: Record,
    test_producer: str,
    namespace: str,
    repo: Path,
    created_at: datetime,
    known: list[Record] | None = None,
    subject: str | None = None,
    run: str | None = None,
    location: str | None = None,
    key_pattern: str = KEY_PATTERN,
    written_from_intent: bool = False,
) -> Recorded:
    """Evidence for each criterion of the approved `intent` that a test names by key, and a test
    artifact per file: reused from `known` records if recorded there, otherwise produced by
    `test_producer`, with the named criteria as self-declared context if `written_from_intent`.
    derivedFrom is intent where R4 and R5 allow it."""
    if intent.get("status") != "approved":
        raise RecordError("approve the intent first: evidence refers to an approved version")
    known = known or []
    reference = records.reference(intent)
    order = [c["key"] for c in intent["criteria"]]
    adapter = records.actor(
        kind="tool",
        name="ois junit (JUnit XML adapter of ois-tools)",
        version=package_version("ois-tools"),
        created_at=created_at,
        id=f"{namespace.rstrip('/')}/actors/ois-junit",
    )
    recorded = Recorded([adapter], records.reference(adapter))
    tracked = [path for path in _git(repo, "ls-files", "-z").split("\0") if path]

    tests: dict[str, list[tuple[TestCase, str]]] = {}  # file -> named criteria
    for case in _dedupe([case for report in reports for case in _cases(report, created_at)]):
        keys = keys_of(case, key_pattern)
        if not keys:
            recorded.without_key += 1
            continue
        path = _test_file(tracked, case)
        if path is None:
            recorded.unmatched.append(f"{_statement(case)}: no test file found in Git")
            continue
        for key in keys:
            if key in order:
                tests.setdefault(path, []).append((case, key))
            else:
                recorded.unmatched.append(
                    f"{_statement(case)}: {key} is not a criterion of {reference}"
                )

    for path, named in tests.items():
        source = records.file_source(repo / path, location)
        context = [f"{reference}#{key}" for key in order if key in {k for _, k in named}]
        test = _known_test(known, source) or records.artifact(
            source,
            kind="test",
            produced_by=test_producer,
            created_at=created_at,
            namespace=namespace,
            context=context if written_from_intent else (),
        )
        recorded.records.append(test)
        for case, key in named:
            evidence = partial(
                records.evidence,
                criterion=f"{reference}#{key}",
                test=test["id"],
                result=case.result,
                recorded_by=recorded.adapter,
                observed_at=case.observed_at,
                created_at=created_at,
                namespace=namespace,
                subject=subject,
                run=run,
            )
            claimed = evidence(derived_from="intent")
            document = _document([intent, *known, test, claimed])
            problems = rules.derivation_problems(document, claimed)
            made = evidence(derived_from="unknown") if problems else claimed
            if problems and "context" in test:
                recorded.notes.append(f"{key}: {_statement(case)}: {problems[0]}")
            if made not in recorded.records:
                recorded.records.append(made)
    return recorded


def _known_test(known: list[Record], source: records.Source) -> Record | None:
    """A test artifact already recorded for this file at this content."""
    for record in known:
        if (
            record["type"] == "artifact"
            and record["kind"] == "test"
            and record["digest"].get("sha256") == source.digest["sha256"]
            and record.get("path", source.path) == source.path
        ):
            return record
    return None


def _document(found: list[Record]) -> Document:
    return Document([Entry(record, f"$.records[{i}]") for i, record in enumerate(found)])


def _cases(report: JUnitReport, now: datetime) -> list[TestCase]:
    root = ElementTree.fromstring(report.content)
    digest = hashlib.sha256(report.content).hexdigest()
    cases = []
    for suite in root.iter("testsuite"):
        observed_at = _observed_at(suite.get("timestamp"), report.written_at or now)
        for case in suite.findall("testcase"):
            cases.append(
                TestCase(
                    classname=case.get("classname", ""),
                    name=case.get("name", ""),
                    file=case.get("file"),
                    result=_result(case),
                    observed_at=observed_at,
                    report=digest,
                    properties=tuple(
                        (p.get("name", ""), p.get("value", "")) for p in case.iter("property")
                    ),
                )
            )
    return cases


def _result(case: ElementTree.Element) -> str:
    if case.find("error") is not None:
        return "error"
    if case.find("failure") is not None:
        return "fail"
    if case.find("skipped") is not None:
        return "skipped"
    return "pass"


def _dedupe(cases: list[TestCase]) -> list[TestCase]:
    """One case per test; a test that ran more than once keeps its worst result."""
    worst: dict[str, TestCase] = {}
    for case in cases:
        known = worst.get(_key(case))
        if known is None or RESULTS.index(case.result) > RESULTS.index(known.result):
            worst[_key(case)] = case
    return list(worst.values())


def _observed_at(timestamp: str | None, fallback: datetime) -> datetime:
    """The suite's timestamp if it names an offset; JUnit often omits it, so otherwise the time
    the report was written."""
    if timestamp:
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is not None:
            return parsed
    return fallback


def _test_file(tracked: list[str], case: TestCase) -> str | None:
    """The tracked file a test case comes from: its file attribute, its class name as a path, or
    the longest prefix of its class name that names exactly one file (com.acme.InvoiceTest ->
    .../com/acme/InvoiceTest.java, tests.test_invoice -> tests/test_invoice.py)."""
    if case.file and case.file in tracked:
        return case.file
    if case.classname in tracked:  # Vitest and Playwright
        return case.classname
    parts = case.classname.split(".")
    for end in range(len(parts), 0, -1):
        stem = "/".join(parts[:end])
        matches = [p for p in tracked if _stem(p) == stem or _stem(p).endswith(f"/{stem}")]
        if len(matches) == 1:
            return matches[0]
    return None


def _stem(path: str) -> str:
    return str(PurePosixPath(path).with_suffix(""))


def _artifact(
    ns: str,
    repo: Path,
    commit: str,
    location: str,
    path: str | None,
    case: TestCase,
    producer: str,
    now: datetime,
) -> Record:
    if path is None:
        # No file found: the artifact is the repository at that commit (section 6.4 allows
        # gitCommit alone for artifacts located in Git).
        anchor = hashlib.sha256(case.classname.encode()).hexdigest()[:8]
        return _record(
            "artifact",
            f"{ns}/artifacts/test-{commit[:12]}-{anchor}",
            now,
            kind="test",
            location=location,
            digest={"gitCommit": commit},
            producedBy=producer,
        )
    content = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{path}"], check=True, capture_output=True
    ).stdout
    sha256 = hashlib.sha256(content).hexdigest()
    # The id follows path and content, so a changed test file is a new artifact (R10).
    anchor = hashlib.sha256(f"{path}\n{sha256}".encode()).hexdigest()[:16]
    return _record(
        "artifact",
        f"{ns}/artifacts/test-{anchor}",
        now,
        kind="test",
        location=location,
        path=path,
        digest={"sha256": sha256, "gitCommit": commit},
        producedBy=producer,
    )


def _key(case: TestCase) -> str:
    """Stable and valid for any test name: TC- and a hash of class and test name."""
    return "TC-" + hashlib.sha256(f"{case.classname}::{case.name}".encode()).hexdigest()[:12]


def _statement(case: TestCase) -> str:
    return f"{case.classname}.{case.name}" if case.classname else case.name


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "suite"


def _record(type_: str, id_: str, now: datetime, **fields: Any) -> Record:
    return {"ois": "0.1", "type": type_, "id": id_, "createdAt": _timestamp(now), **fields}


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise ValueError(
            f"git {' '.join(args)} failed in {repo}: {error.stderr.strip()}"
        ) from error
