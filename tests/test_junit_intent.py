"""ois junit --intent. tests/junit/: real reports of pytest 9.2.0, Vitest 5.0.5, Jest 30.5.2 with
jest-junit 17.0.0, Playwright 1.64.1, JUnit 6.1.3 with Surefire 3.6.1, Go 1.27.3 and .NET SDK
10.0.403 with xUnit and MSTest (failure texts shortened, paths those of a CI checkout).
Each run has AC-1 passing, AC-2 failing and a test without a key; Playwright names AC-1 by its tag
option and JUnit AC-2 by @Tag, neither of which reaches the report."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ois_tools import validate
from ois_tools.cli import main as ois
from ois_tools.formats import JUnitReport
from ois_tools.formats import TestCase as Case
from ois_tools.intents import approve, seal
from ois_tools.junit import Options, keys_of, record_run, to_bundle

HERE = Path(__file__).parent / "junit"
NS = "https://ois.acme.example"
INTENT = f"{NS}/intents/INT-7"
PO = f"{NS}/actors/po-7f3a"
QE = f"{NS}/actors/qe-2c9d"
DEV = f"{NS}/actors/dev-agent@2.1"
RUNTIME = f"{NS}/actors/agent-runtime@4.3"
AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
FILES = [
    "tests/test_rounding.py",
    "tests/rounding.test.js",
    "jest/rounding.test.cjs",
    "tests/rounding.spec.ts",
    "src/test/java/com/acme/billing/RoundingTest.java",
    "src/main/java/com/acme/billing/Rounding.java",
    "Billing.Tests/RoundingTests.cs",
]
GO = {
    "go.mod": "module git.acme.example/billing\n\ngo 1.27\n",
    "rounding/rounding.go": "package rounding\n",
    "rounding/rounding_test.go": "package rounding\n\nfunc TestRounding(t *testing.T) {}\n"
    "func TestWithoutKey(t *testing.T) {}\nfunc TestSkipped(t *testing.T) {}\n",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "billing"
    for name in FILES:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(f"// {name}\n")
    for name, text in GO.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    git(root, "init", "-q")
    git(root, "remote", "add", "origin", "https://git.acme.example/billing.git")
    git(root, "add", ".")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init")
    return root


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def approved(with_decision: bool = False) -> Any:
    draft = {
        "ois": "0.1",
        "type": "intent",
        "id": INTENT,
        "createdAt": "2026-10-29T08:00:00Z",
        "version": 1,
        "title": "Invoices round to five Rappen",
        "status": "draft",
        "criteria": [
            {"key": key, "statement": text, "notation": "text", "kind": "functional"}
            for key, text in [("AC-1", "Totals round down"), ("AC-2", "Negatives are rejected")]
        ],
    }
    intent, decision = approve(seal(draft), decided_by=PO, decided_at=AT)
    return (intent, decision) if with_decision else intent


def report(name: str) -> JUnitReport:
    return JUnitReport((HERE / name).read_bytes(), written_at=AT)


def run(repo: Path, *names: str, **options: Any) -> Any:
    options.setdefault("intent", approved())
    options.setdefault("test_producer", QE)
    return record_run(
        [report(name) for name in names],
        namespace=NS,
        repo=repo,
        created_at=AT,
        **options,
    )


def by_type(records: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [r for r in records if r["type"] == kind]


def results(records: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    return {
        e["criterion"].rpartition("#")[2]: (e["result"], e["derivedFrom"])
        for e in by_type(records, "evidence")
    }


def bundle(*recorded: dict[str, Any]) -> dict[str, Any]:
    """The records with the intent, its approval and the actors they name."""
    from ois_tools import records

    people = [records.actor(kind="human", name=n, created_at=AT, id=i) for n, i in
              [("PO", PO), ("QE", QE)]]  # fmt: skip
    dev = records.actor(
        kind="agent",
        name="Dev agent",
        version="2.1",
        created_at=AT,
        id=DEV.partition("@")[0],
        models=[records.model("acme-ai:coder:2")],
    )
    return {
        "ois": "0.1",
        "type": "bundle",
        "id": f"{NS}/bundles/b",
        "createdAt": "2026-10-29T12:00:00Z",
        "records": [*approved(with_decision=True), *people, dev, *recorded],
    }


def violations(document: dict[str, Any]) -> list[str]:
    return [f"{r.rule}: {r.message}" for r in validate(document).reports if r.level == "violation"]


# --- Criterion keys -----------------------------------------------------------------------------


def case(name: str, properties: tuple[tuple[str, str], ...] = ()) -> Case:
    return Case("c", name, None, "pass", AT, "r", properties)


def test_keys_from_the_name_and_from_properties() -> None:
    assert keys_of(case("AC-1 rounds down")) == ["AC-1"]
    assert keys_of(case("Invoices > rejects negative amounts @AC-2")) == ["AC-2"]
    assert keys_of(case("rounds", (("criterion", "AC-1"), ("criterion", "AC-3")))) == [
        "AC-1",
        "AC-3",
    ]
    assert keys_of(case("AC-1 and AC-1 again", (("criterion", "AC-1"),))) == ["AC-1"]
    assert keys_of(case("without a key", (("owner", "AC-9"),))) == []


def test_a_key_is_a_whole_word() -> None:
    assert keys_of(case("xAC-1 or AC-12 or AC-1x")) == ["AC-12"]
    assert keys_of(case("REQ-12 only"), r"REQ-[0-9]+") == ["REQ-12"]


# --- Every runner -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("pytest.xml", "tests/test_rounding.py"),
        ("vitest.xml", "tests/rounding.test.js"),
        ("jest.xml", "jest/rounding.test.cjs"),
        ("playwright.xml", "tests/rounding.spec.ts"),
        ("surefire.xml", "src/test/java/com/acme/billing/RoundingTest.java"),
        ("playwright.json", "tests/rounding.spec.ts"),
        ("jest.json", "jest/rounding.test.cjs"),
        ("vitest.json", "tests/rounding.test.js"),
        ("go.json", "rounding/rounding_test.go"),
        ("xunit.trx", "Billing.Tests/RoundingTests.cs"),
        ("mstest.trx", "Billing.Tests/RoundingTests.cs"),
    ],
)
def test_each_runner_gives_evidence_for_its_keys(repo: Path, name: str, path: str) -> None:
    recorded = run(repo, name)
    [test] = by_type(recorded.records, "artifact")
    assert test["path"] == path
    assert test["producedBy"] == QE
    expected = {"AC-1": ("pass", "unknown"), "AC-2": ("fail", "unknown")}
    if name == "playwright.xml":  # the tag option does not reach JUnit XML, the annotation does
        expected = {"AC-2": ("fail", "unknown")}
    if name in ("surefire.xml", "xunit.trx"):  # display names reach them, tags and traits not
        expected = {"AC-1": ("pass", "unknown")}
    if name == "mstest.trx":  # [TestCategory] reaches TRX, the display name does not
        expected = {"AC-2": ("fail", "unknown")}
    assert results(recorded.records) == expected
    assert recorded.without_key >= 1
    assert validate(bundle(*recorded.records)).conforms


@pytest.mark.parametrize("name", sorted(p.name for p in HERE.iterdir()))
def test_each_format_gives_a_level_1_bundle(repo: Path, name: str) -> None:
    options = Options(namespace=NS, suite="Team Billing", repo=repo, now=AT)
    made = to_bundle([report(name)], options)
    assert validate(made).conforms
    tests = [r for r in made["records"] if r["type"] == "artifact"]
    assert all("path" in t for t in tests)  # every test file found
    assert not any("/home/runner" in json.dumps(r) for r in made["records"])


def test_evidence_names_criterion_test_subject_and_run(repo: Path) -> None:
    recorded = run(repo, "pytest.xml", subject=f"{NS}/artifacts/build-1", run="https://ci/1")
    evidence = by_type(recorded.records, "evidence")[0]
    assert evidence["criterion"] == f"{INTENT}@1#AC-1"
    assert evidence["test"] == by_type(recorded.records, "artifact")[0]["id"]
    assert evidence["subject"] == f"{NS}/artifacts/build-1"
    assert evidence["run"] == "https://ci/1"
    assert evidence["recordedBy"] == recorded.adapter
    assert evidence["observedAt"] == "2026-10-29T09:25:59Z"


def test_a_key_that_is_not_a_criterion_gives_no_evidence(repo: Path) -> None:
    intent = approved()
    intent["criteria"] = intent["criteria"][1:]  # AC-2 only
    recorded = run(repo, "pytest.xml", intent=intent)
    assert set(results(recorded.records)) == {"AC-2"}
    assert any("AC-1 is not a criterion" in u for u in recorded.unmatched)


def test_a_test_without_a_file_gives_no_evidence(repo: Path) -> None:
    git(repo, "rm", "-q", "tests/test_rounding.py")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "rm")
    recorded = run(repo, "pytest.xml")
    assert by_type(recorded.records, "evidence") == []
    assert any("no test file" in u for u in recorded.unmatched)


def test_a_draft_intent_is_refused(repo: Path) -> None:
    draft = approved() | {"status": "draft"}
    with pytest.raises(ValueError, match="approve the intent first"):
        run(repo, "pytest.xml", intent=draft)


# --- Derivation ---------------------------------------------------------------------------------


def test_tests_written_from_the_intent_declare_it(repo: Path) -> None:
    recorded = run(repo, "pytest.xml", written_from_intent=True)
    [test] = by_type(recorded.records, "artifact")
    assert test["context"] == [f"{INTENT}@1#AC-1", f"{INTENT}@1#AC-2"]
    assert "contextRecordedBy" not in test  # self-declared (R12)
    assert results(recorded.records) == {"AC-1": ("pass", "intent"), "AC-2": ("fail", "intent")}
    result = validate(bundle(*recorded.records))
    assert result.conforms
    assert {r.rule for r in result.reports} == {"R12"}


def test_a_recorded_test_artifact_is_reused(repo: Path) -> None:
    from ois_tools import records

    path = repo / "tests" / "test_rounding.py"
    known = records.artifact(
        records.file_source(path),
        kind="test",
        produced_by=QE,
        created_at=AT,
        namespace=NS,
        context=[f"{INTENT}@1"],
        context_recorded_by=RUNTIME,
    )
    recorded = run(repo, "pytest.xml", known=[known])
    assert by_type(recorded.records, "artifact") == [known]
    assert results(recorded.records) == {"AC-1": ("pass", "intent"), "AC-2": ("fail", "intent")}


def test_a_test_that_saw_the_code_is_not_derived_from_the_intent(repo: Path) -> None:
    from ois_tools import records

    code = records.artifact(
        records.file_source(repo / "src/main/java/com/acme/billing/Rounding.java"),
        kind="code",
        produced_by=DEV,
        created_at=AT,
        namespace=NS,
    )
    known = records.artifact(
        records.file_source(repo / "tests" / "test_rounding.py"),
        kind="test",
        produced_by=QE,
        created_at=AT,
        namespace=NS,
        context=[f"{INTENT}@1", code["id"]],
        context_recorded_by=RUNTIME,
    )
    recorded = run(repo, "pytest.xml", known=[known, code], written_from_intent=True)
    assert results(recorded.records) == {"AC-1": ("pass", "unknown"), "AC-2": ("fail", "unknown")}
    assert any("includes code" in note for note in recorded.notes)


def test_a_test_by_the_producer_of_its_subject_is_not_derived_from_the_intent(repo: Path) -> None:
    from ois_tools import records

    build = records.artifact(
        records.commit_source(repo), kind="build", produced_by=DEV, created_at=AT, namespace=NS
    )
    recorded = run(
        repo,
        "pytest.xml",
        test_producer=DEV,
        subject=build["id"],
        known=[build],
        written_from_intent=True,
    )
    assert results(recorded.records) == {"AC-1": ("pass", "unknown"), "AC-2": ("fail", "unknown")}
    assert any("both produced by" in note for note in recorded.notes)
    assert violations(bundle(build, *recorded.records)) == []


def test_a_retried_test_keeps_its_worst_result(repo: Path) -> None:
    text = (HERE / "pytest.xml").read_text(encoding="utf-8")
    passing = text.replace('<failure message="assert 1 == 2">shortened</failure>', "")
    reports = [JUnitReport(passing.encode(), AT), report("pytest.xml")]
    recorded = record_run(
        reports, intent=approved(), test_producer=QE, namespace=NS, repo=repo, created_at=AT
    )
    assert results(recorded.records)["AC-2"] == ("fail", "unknown")
    assert len(by_type(recorded.records, "evidence")) == 2


# --- Command line -------------------------------------------------------------------------------


def write_intent(directory: Path) -> None:
    (directory / "intents").mkdir(parents=True)
    path = directory / "intents" / "INT-7-v1.ois.json"
    path.write_text(json.dumps(approved()), encoding="utf-8")


def test_cli_records_evidence_for_an_intent(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "ois"
    write_intent(out)
    args = ["junit", str(HERE / "pytest.xml"), "--intent", f"{INTENT}@1", "--test-producer", QE]
    args += ["--namespace", NS, "--repo", str(repo), "--output-dir", str(out)]
    assert ois([*args, "--written-from-intent"]) == 0
    err = capsys.readouterr().err
    assert "2 results: 2 derived from the intent, 0 not" in err
    assert "2 tests without a criterion key" in err
    assert len(list((out / "evidence").iterdir())) == 2
    assert ois([*args, "--written-from-intent"]) == 0  # unchanged on a second run
    assert "unchanged" in capsys.readouterr().err


def test_cli_intent_mode_needs_a_test_producer_and_no_output(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["junit", str(HERE / "pytest.xml"), "--namespace", NS, "--repo", str(repo)]
    intent = ["--intent", f"{INTENT}@1"]
    for args, message in [
        (intent, "--intent needs --test-producer"),
        ([*intent, "--test-producer", QE, "--output", "b.json"], "--output writes a bundle"),
        ([], "pass --suite, or --intent"),
    ]:
        with pytest.raises(SystemExit) as exit_info:
            ois([*base, *args])
        assert exit_info.value.code == 2
        assert message in capsys.readouterr().err
