"""Test reports read by ois junit, recognized by content: JUnit XML, TRX (.NET), Playwright JSON,
Jest and Vitest JSON, and go test -json. Only names, results, times, tags and file paths are read;
messages and output are not."""

import hashlib
import json
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

TRX = "{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}"


@dataclass(frozen=True)
class JUnitReport:
    content: bytes
    written_at: datetime | None = None  # file modification time, for timestamps without offset


@dataclass(frozen=True)
class TestCase:
    classname: str
    name: str
    file: str | None  # as the report gives it, absolute or relative
    result: str
    observed_at: datetime
    report: str  # sha256 of the report the case comes from
    properties: tuple[tuple[str, str], ...] = ()
    tags: tuple[str, ...] = ()  # without "@"
    format: str = "junit"


def cases(report: JUnitReport, now: datetime) -> list[TestCase]:
    """The test cases of a report, whatever its format."""
    text = report.content.lstrip(b"\xef\xbb\xbf \t\r\n")
    digest = hashlib.sha256(report.content).hexdigest()
    fallback = report.written_at or now
    if text.startswith(b"<"):
        root = ElementTree.fromstring(report.content)
        if root.tag == f"{TRX}TestRun":
            return _trx(root, digest, fallback)
        if root.tag in ("testsuites", "testsuite"):
            return _junit(root, digest, fallback)
    elif text.startswith(b"{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return _go(text, digest)
        if isinstance(data, dict) and "suites" in data and "config" in data:
            return _playwright(data, digest)
        if isinstance(data, dict) and isinstance(data.get("testResults"), list):
            return _jest(data, digest, fallback)
        if isinstance(data, dict) and "Action" in data:
            return _go(text, digest)
    raise ValueError(
        "not a test report: expected JUnit XML, TRX, Playwright JSON, Jest or Vitest JSON, "
        "or go test -json"
    )


def _junit(root: ElementTree.Element, digest: str, fallback: datetime) -> list[TestCase]:
    found = []
    for suite in root.iter("testsuite"):
        observed_at = _observed_at(suite.get("timestamp"), fallback)
        for case in suite.findall("testcase"):
            found.append(
                TestCase(
                    classname=case.get("classname", ""),
                    name=case.get("name", ""),
                    file=case.get("file"),
                    result=_junit_result(case),
                    observed_at=observed_at,
                    report=digest,
                    properties=tuple(
                        (p.get("name", ""), p.get("value", "")) for p in case.iter("property")
                    ),
                )
            )
    return found


def _junit_result(case: ElementTree.Element) -> str:
    if case.find("error") is not None:
        return "error"
    if case.find("failure") is not None:
        return "fail"
    if case.find("skipped") is not None:
        return "skipped"
    return "pass"


def _observed_at(timestamp: str | None, fallback: datetime) -> datetime:
    """The suite's timestamp if it names an offset; JUnit often omits it, so otherwise the time
    the report was written."""
    if timestamp:
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is not None:
            return parsed
    return fallback


TRX_RESULTS = {"Passed": "pass", "PassedButRunAborted": "pass", "Failed": "fail", "Timeout": "fail",
               "Error": "error", "Aborted": "error", "Disconnected": "error"}  # fmt: skip


def _trx(root: ElementTree.Element, digest: str, fallback: datetime) -> list[TestCase]:
    """UnitTestResult per test; class and categories come from its UnitTest definition."""
    definitions = {test.get("id"): test for test in root.iter(f"{TRX}UnitTest")}
    found = []
    for result in root.iter(f"{TRX}UnitTestResult"):
        definition = definitions.get(result.get("testId"))
        method = definition.find(f"{TRX}TestMethod") if definition is not None else None
        categories = (
            [item.get("TestCategory", "") for item in definition.iter(f"{TRX}TestCategoryItem")]
            if definition is not None
            else []
        )
        end = result.get("endTime")
        found.append(
            TestCase(
                classname=method.get("className", "") if method is not None else "",
                name=result.get("testName", ""),
                file=None,
                result=TRX_RESULTS.get(result.get("outcome", ""), "skipped"),
                observed_at=datetime.fromisoformat(end) if end else fallback,
                report=digest,
                tags=tuple(c for c in categories if c),
                format="trx",
            )
        )
    return found


def _playwright(data: dict[str, Any], digest: str) -> list[TestCase]:
    """The final attempt of each test, per project; titles of describe blocks join the name."""
    root = data["config"].get("rootDir", "")
    found = []

    def walk(suite: dict[str, Any], titles: list[str]) -> None:
        inner = titles if suite.get("file") == suite.get("title") else [*titles, suite["title"]]
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                if not test.get("results"):
                    continue
                last = test["results"][-1]
                project = test.get("projectName") or ""
                name = " > ".join([*inner, spec["title"]]) + (f" [{project}]" if project else "")
                start = datetime.fromisoformat(last["startTime"].replace("Z", "+00:00"))
                found.append(
                    TestCase(
                        classname=spec["file"],
                        name=name,
                        file=f"{root.rstrip('/')}/{spec['file']}" if root else spec["file"],
                        result=_playwright_result(test["status"], last["status"]),
                        observed_at=start + timedelta(milliseconds=last.get("duration", 0)),
                        report=digest,
                        properties=tuple(
                            (a.get("type", ""), a.get("description", ""))
                            for a in test.get("annotations", [])
                        ),
                        tags=tuple(tag.lstrip("@") for tag in spec.get("tags", [])),
                        format="playwright",
                    )
                )
        for child in suite.get("suites", []):
            walk(child, inner)

    for suite in data["suites"]:
        walk(suite, [])
    return found


def _playwright_result(outcome: str, last: str) -> str:
    if outcome == "skipped":
        return "skipped"
    if outcome in ("expected", "flaky"):
        return "pass"
    return "error" if last == "interrupted" else "fail"


JEST_RESULTS = {"passed": "pass", "failed": "fail"}  # pending, todo, skipped, disabled: skipped


def _jest(data: dict[str, Any], digest: str, fallback: datetime) -> list[TestCase]:
    """Each assertion result; Vitest's JSON reporter writes the same structure, with tags."""
    found = []
    for file in data["testResults"]:
        end = file.get("endTime")
        observed_at = datetime.fromtimestamp(end / 1000, UTC) if end else fallback
        for test in file.get("assertionResults", []):
            titles = [*test.get("ancestorTitles", []), test["title"]]
            name = test.get("fullName") or " ".join(titles)
            found.append(
                TestCase(
                    classname="",
                    name=name,
                    file=file["name"],
                    result=JEST_RESULTS.get(test["status"], "skipped"),
                    observed_at=observed_at,
                    report=digest,
                    tags=tuple(test.get("tags") or ()),
                    format="jest",
                )
            )
    return found


GO_RESULTS = {"pass": "pass", "fail": "fail", "skip": "skipped"}


def _go(text: bytes, digest: str) -> list[TestCase]:
    """The final action of each test and subtest; a test that started and never finished, for
    example after a panic, is an error."""
    finished: dict[tuple[str, str], tuple[str, datetime]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"not a test report: go test -json line: {error}") from error
        if not isinstance(event, dict) or "Action" not in event:
            raise ValueError("not a test report: go test -json line without Action")
        if not event.get("Test"):
            continue
        key = (event.get("Package", ""), event["Test"])
        moment = datetime.fromisoformat(event["Time"].replace("Z", "+00:00"))
        if event["Action"] == "run":
            finished.setdefault(key, ("error", moment))
        elif event["Action"] in GO_RESULTS:
            finished[key] = (GO_RESULTS[event["Action"]], moment)
    return [
        TestCase(package, test, None, result, moment, digest, format="go")
        for (package, test), (result, moment) in finished.items()
    ]
