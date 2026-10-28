import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ois_tools import validate
from ois_tools.junit import JUnitReport, Options, location_of, to_bundle
from ois_tools.junit_cli import main

NS = "https://ois.acme.example"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

PYTEST_REPORT = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="1" skipped="1" tests="4"
             timestamp="2026-10-28T10:15:00.123456+00:00">
    <testcase classname="tests.test_invoice" name="test_rounds_to_five_rappen"
              file="tests/test_invoice.py" line="3" time="0.01"/>
    <testcase classname="tests.test_invoice" name="test_rejects_negative_amounts"
              file="tests/test_invoice.py" line="7" time="0.01">
      <failure message="assert 1 == 2">AssertionError</failure>
    </testcase>
    <testcase classname="tests.test_invoice" name="test_currency_from_locale"
              file="tests/test_invoice.py" line="11" time="0.00">
      <skipped message="no locale"/>
    </testcase>
    <testcase classname="tests.test_invoice" name="test_database_timeout"
              file="tests/test_invoice.py" line="15" time="0.00">
      <error message="TimeoutError">Traceback</error>
    </testcase>
  </testsuite>
</testsuites>
"""

SUREFIRE_REPORT = b"""<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="com.acme.billing.InvoiceTest" tests="2" failures="0" errors="0"
           skipped="0" timestamp="2026-10-28T11:15:00">
  <testcase name="roundsToFiveRappen" classname="com.acme.billing.InvoiceTest" time="0.002"/>
  <testcase name="vanished" classname="com.acme.billing.RemovedTest" time="0.001"/>
</testsuite>
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_invoice.py").write_text("def test_x():\n    pass\n")
    java = tmp_path / "src" / "test" / "java" / "com" / "acme" / "billing"
    java.mkdir(parents=True)
    (java / "InvoiceTest.java").write_text("class InvoiceTest {}\n")
    git("init", "-q")
    git("remote", "add", "origin", "https://ci-user:s3cret@git.acme.example/billing.git")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init")
    return tmp_path


def commit_of(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def convert(repo: Path, *reports: bytes, **options: Any) -> dict[str, Any]:
    opts = Options(namespace=NS, suite="Team Billing", repo=repo, now=NOW, **options)
    return to_bundle([JUnitReport(r, written_at=NOW) for r in reports], opts)


def of_type(bundle: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [r for r in bundle["records"] if r["type"] == kind]


def test_pytest_report_becomes_a_conforming_bundle(repo: Path) -> None:
    bundle = convert(repo, PYTEST_REPORT)
    assert validate(bundle).reports == []
    [intent] = of_type(bundle, "intent")
    assert intent["status"] == "draft"
    assert [c["statement"] for c in intent["criteria"]] == [
        "tests.test_invoice.test_rounds_to_five_rappen",
        "tests.test_invoice.test_rejects_negative_amounts",
        "tests.test_invoice.test_currency_from_locale",
        "tests.test_invoice.test_database_timeout",
    ]
    assert [e["result"] for e in of_type(bundle, "evidence")] == [
        "pass",
        "fail",
        "skipped",
        "error",
    ]
    assert {e["derivedFrom"] for e in of_type(bundle, "evidence")} == {"unknown"}


def test_quick_start_actors(repo: Path) -> None:
    bundle = convert(repo, PYTEST_REPORT)
    adapter, suite = of_type(bundle, "actor")
    assert (adapter["kind"], suite["kind"]) == ("tool", "tool")
    assert suite["name"] == "Team Billing test suite"
    [artifact] = of_type(bundle, "artifact")
    assert artifact["producedBy"] == f"{suite['id']}@{suite['version']}"
    for evidence in of_type(bundle, "evidence"):
        assert evidence["recordedBy"] == f"{adapter['id']}@{adapter['version']}"


def test_test_file_from_the_file_attribute(repo: Path) -> None:
    [artifact] = of_type(convert(repo, PYTEST_REPORT), "artifact")
    content = (repo / "tests" / "test_invoice.py").read_bytes()
    assert artifact["kind"] == "test"
    assert artifact["path"] == "tests/test_invoice.py"
    assert artifact["digest"] == {
        "sha256": hashlib.sha256(content).hexdigest(),
        "gitCommit": commit_of(repo),
    }


def test_test_file_from_the_class_name(repo: Path) -> None:
    found, missing = of_type(convert(repo, SUREFIRE_REPORT), "artifact")
    assert found["path"] == "src/test/java/com/acme/billing/InvoiceTest.java"
    assert "sha256" in found["digest"]
    assert "path" not in missing
    assert missing["digest"] == {"gitCommit": commit_of(repo)}


def test_artifact_id_follows_content(repo: Path) -> None:
    first = of_type(convert(repo, PYTEST_REPORT), "artifact")[0]["id"]
    (repo / "tests" / "test_invoice.py").write_text("def test_y():\n    pass\n")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "commit", "-qam", "change"], check=True)
    second = of_type(convert(repo, PYTEST_REPORT), "artifact")[0]["id"]
    assert first != second


def test_criterion_keys_are_valid_and_stable(repo: Path) -> None:
    long_name = "test_" + "x" * 80 + "[de-CH-1.5 CHF]"
    report = PYTEST_REPORT.replace(b"test_rounds_to_five_rappen", long_name.encode())
    keys = [c["key"] for c in of_type(convert(repo, report), "intent")[0]["criteria"]]
    again = [c["key"] for c in of_type(convert(repo, report), "intent")[0]["criteria"]]
    assert keys == again
    assert all(re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key) for key in keys)
    assert len(set(keys)) == len(keys)


def test_observed_at_from_the_suite_timestamp_with_offset(repo: Path) -> None:
    evidence = of_type(convert(repo, PYTEST_REPORT), "evidence")[0]
    assert evidence["observedAt"] == "2026-10-28T10:15:00Z"


def test_observed_at_from_the_report_file_without_offset(repo: Path) -> None:
    evidence = of_type(convert(repo, SUREFIRE_REPORT), "evidence")[0]
    assert evidence["observedAt"] == "2026-10-28T12:00:00Z"


def test_location_strips_credentials(repo: Path) -> None:
    assert location_of(repo) == f"git+https://git.acme.example/billing.git@{commit_of(repo)}"


def test_location_of_an_scp_like_remote(repo: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "remote", "set-url", "origin", "git@github.com:acme/billing.git"],
        check=True,
    )
    assert location_of(repo) == f"git+ssh://github.com/acme/billing.git@{commit_of(repo)}"


def test_cli_writes_a_validated_bundle(
    repo: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path_factory.mktemp("reports") / "junit.xml"
    report.write_bytes(PYTEST_REPORT)
    args = [str(report), "--namespace", NS, "--suite", "Team Billing", "--repo", str(repo)]
    assert main(args) == 0
    assert validate(json.loads(capsys.readouterr().out)).conforms
