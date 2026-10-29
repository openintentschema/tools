"""Test reports to OIS: a Level 1 bundle (section 11.4), or evidence for the criteria of an
approved intent that tests name by key. formats.py reads the reports."""

import hashlib
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from importlib.metadata import version as package_version
from pathlib import Path, PurePosixPath
from typing import Any

from ois_tools import records
from ois_tools.formats import JUnitReport, TestCase, cases
from ois_tools.records import KEY_PATTERN, Recorded, RecordError, location_of

Record = dict[str, Any]

RESULTS = ("pass", "skipped", "fail", "error")  # from best to worst
KEY_PROPERTY = "criterion"  # <property name="criterion" value="AC-1"/> on a test case


@dataclass(frozen=True)
class Options:
    namespace: str  # URI under the producer's domain, e.g. https://ois.acme.example
    suite: str  # team or suite name; the test suite actor is named after it
    repo: Path  # Git working tree that holds the test files
    location: str | None = None  # defaults to git+<origin>@<HEAD>
    run: str | None = None  # link to the pipeline run
    now: datetime | None = None


def to_bundle(reports: list[JUnitReport], options: Options) -> Record:
    """Build the bundle; the caller validates it before passing it on."""
    now = (options.now or datetime.now(UTC)).astimezone(UTC)
    ns = options.namespace.rstrip("/")
    commit = _git(options.repo, "rev-parse", "HEAD")
    location = options.location or location_of(options.repo)
    tracked = [path for path in _git(options.repo, "ls-files", "-z").split("\0") if path]
    found = _dedupe(_located(reports, now, options.repo, tracked))

    adapter = _record(
        "actor",
        f"{ns}/actors/ois-junit",
        now,
        kind="tool",
        name="ois junit (test report adapter of ois-tools)",
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
            for c in found
        ],
    )

    artifacts: dict[str, Record] = {}
    evidence = []
    for case in found:
        path = case.file
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

    combined = hashlib.sha256("".join(c.report for c in found).encode()).hexdigest()
    records = [adapter, suite, intent, *artifacts.values(), *evidence]
    return _record("bundle", f"{ns}/bundles/junit-{combined[:16]}", now, records=records)


def keys_of(case: TestCase, pattern: str = KEY_PATTERN) -> list[str]:
    """Criterion keys of a test: its `criterion` properties, its tags, then keys in its name
    between non-alphanumeric characters. Group 1 of the pattern, if it has one, is the key."""
    found = [
        key
        for name, value in case.properties
        if name == KEY_PROPERTY
        for key in re.split(r"[\s,]+", value)
        if key
    ]
    for tag in case.tags:
        if match := re.fullmatch(pattern, tag):
            found.append(match[1] if match.re.groups else match[0])
    for match in re.finditer(rf"(?<![A-Za-z0-9-])(?:{pattern})(?![A-Za-z0-9])", case.name):
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
        name="ois junit (test report adapter of ois-tools)",
        version=package_version("ois-tools"),
        created_at=created_at,
        id=f"{namespace.rstrip('/')}/actors/ois-junit",
    )
    recorded = Recorded([adapter], records.reference(adapter))
    tracked = [path for path in _git(repo, "ls-files", "-z").split("\0") if path]

    tests: dict[str, list[tuple[TestCase, str]]] = {}  # file -> named criteria
    for case in _dedupe(_located(reports, created_at, repo, tracked)):
        keys = keys_of(case, key_pattern)
        if not keys:
            recorded.without_key += 1
            continue
        path = case.file
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
        test = records.known_test(known, source) or records.artifact(
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
            made, problems = records.derived(evidence, [intent, *known, test])
            if problems and "context" in test:
                recorded.notes.append(f"{key}: {_statement(case)}: {problems[0]}")
            if made not in recorded.records:
                recorded.records.append(made)
    return recorded


def _dedupe(cases: list[TestCase]) -> list[TestCase]:
    """One case per test; a test that ran more than once keeps its worst result."""
    worst: dict[str, TestCase] = {}
    for case in cases:
        known = worst.get(_key(case))
        if known is None or RESULTS.index(case.result) > RESULTS.index(known.result):
            worst[_key(case)] = case
    return list(worst.values())


def _located(
    reports: list[JUnitReport], now: datetime, repo: Path, tracked: list[str]
) -> list[TestCase]:
    """The cases of the reports, each with its tracked test file or None; a case without a class
    name is named after its file."""
    found = []
    for report in reports:
        for case in cases(report, now):
            path = _test_file(tracked, case, repo)
            found.append(replace(case, file=path, classname=case.classname or path or ""))
    return found


def _test_file(tracked: list[str], case: TestCase, repo: Path) -> str | None:
    """The tracked file of a test case: its file (also absolute or from another checkout), its
    class name as a path, its Go package and function, or a part of its class name naming one."""
    if case.file:
        if case.file in tracked:
            return case.file
        given = PurePosixPath(case.file)
        if given.is_absolute():
            try:
                relative = Path(case.file).resolve().relative_to(repo.resolve()).as_posix()
            except ValueError:
                relative = None
            if relative in tracked:
                return relative
        ends = [p for p in tracked if case.file.endswith(f"/{p}")]
        if ends:
            return max(ends, key=len)
    if case.classname in tracked:  # Vitest and Playwright
        return case.classname
    if case.format == "go":
        return _go_test_file(tracked, case, repo)
    parts = case.classname.split(".")
    for end in range(len(parts), 0, -1):
        stem = "/".join(parts[:end])
        matches = [p for p in tracked if _stem(p) == stem or _stem(p).endswith(f"/{stem}")]
        if len(matches) == 1:
            return matches[0]
    matches = [p for p in tracked if PurePosixPath(p).stem == parts[-1]]
    return matches[0] if len(matches) == 1 and parts[-1] else None


def _go_test_file(tracked: list[str], case: TestCase, repo: Path) -> str | None:
    """The _test.go file in the package's directory that declares the top-level test."""
    modules = {}
    for path in tracked:
        if PurePosixPath(path).name == "go.mod":
            declared = re.search(r"^module\s+(\S+)", (repo / path).read_text("utf-8"), re.M)
            if declared:
                modules[declared[1]] = str(PurePosixPath(path).parent)
    owners = [m for m in modules if case.classname == m or case.classname.startswith(f"{m}/")]
    if not owners:
        return None
    module = max(owners, key=len)
    directory = PurePosixPath(modules[module], case.classname[len(module) :].lstrip("/"))
    function = f"func {case.name.split('/')[0]}("
    matches = [
        p
        for p in tracked
        if PurePosixPath(p).parent == directory
        and p.endswith("_test.go")
        and function in (repo / p).read_text("utf-8", errors="replace")
    ]
    return matches[0] if len(matches) == 1 else None


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
    if not case.classname:
        return case.name
    return f"{case.classname}{' > ' if '/' in case.classname else '.'}{case.name}"


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
