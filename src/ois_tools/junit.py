"""Level 1 adapter: turn JUnit XML reports into an OIS bundle (spec section 11.4).

The bundle holds what the quick start asks for: an actor for this adapter, an actor for the
existing test suite, one characterization intent in draft with a criterion per test case, an
artifact per test file and an evidence record per test result with derivedFrom unknown.

JUnit XML comes from the producer's own pipeline. It is parsed with the standard library, whose
Expat (2.4.1 and later) refuses entity expansion attacks and does not load external entities.
"""

import hashlib
import re
import subprocess
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit

Record = dict[str, Any]

RESULTS = ("pass", "skipped", "fail", "error")  # from best to worst


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


def location_of(repo: Path) -> str:
    """git+<origin URL>@<HEAD commit>, without credentials; scp-like remotes become ssh URIs."""
    remote = _git(repo, "remote", "get-url", "origin")
    commit = _git(repo, "rev-parse", "HEAD")
    if "://" not in remote and (scp := re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", remote)):
        remote = f"ssh://{scp[1]}/{scp[2]}"
    parts = urlsplit(remote)
    if parts.scheme not in ("https", "http", "ssh", "git") or not parts.hostname:
        raise ValueError(f"cannot turn remote {remote!r} into a location; pass --location")
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return f"git+{urlunsplit((parts.scheme, host, parts.path, '', ''))}@{commit}"


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
    """The tracked file a test case comes from: its file attribute, or the longest prefix of its
    class name that names exactly one file (com.acme.InvoiceTest -> .../com/acme/InvoiceTest.java,
    tests.test_invoice -> tests/test_invoice.py)."""
    if case.file and case.file in tracked:
        return case.file
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
