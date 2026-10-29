"""Gherkin and Cucumber Messages. rounding.ndjson: a real cucumber-js 13.3.0 run of
rounding.feature (AC-1, AC-2 pass; AC-3 fails; AC-4 undefined; one scenario without key)."""

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ois_tools import validate
from ois_tools.cli import main as ois
from ois_tools.cucumber import (
    CucumberError,
    draft_intent,
    record_run,
    result_of,
    statement,
)
from ois_tools.intents import approve

HERE = Path(__file__).parent / "cucumber"
FEATURE = (HERE / "rounding.feature").read_text(encoding="utf-8")
MESSAGES = (HERE / "rounding.ndjson").read_text(encoding="utf-8").splitlines()
NS = "https://ois.acme.example"
INTENT = f"{NS}/intents/INT-7"
PO = f"{NS}/actors/po-7f3a"
QE = f"{NS}/actors/qe-agent@2.1"
AT = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "billing"
    (root / "features").mkdir(parents=True)
    shutil.copy(HERE / "rounding.feature", root / "features" / "rounding.feature")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-q")
    git("remote", "add", "origin", "https://git.acme.example/billing.git")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init")
    return root


def approved(feature: str = FEATURE) -> dict[str, Any]:
    draft = draft_intent([feature], id=INTENT, created_at=AT)
    intent, _ = approve(draft, decided_by=PO, decided_at=AT)
    return intent


def by_type(records: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [r for r in records if r["type"] == kind]


# --- Gherkin to criteria ------------------------------------------------------------------------


def test_a_scenario_with_its_background_is_the_statement() -> None:
    intent = draft_intent([FEATURE], id=INTENT, created_at=AT)
    first = intent["criteria"][0]
    assert first["key"] == "AC-1"
    assert first["notation"] == "gherkin"
    assert first["statement"] == (
        "Given the currency is CHF\n"
        "Given an invoice total of CHF 10.02\n"
        "When the invoice is issued\n"
        "Then the amount due is CHF 10.00"
    )


def test_an_outline_keeps_its_examples() -> None:
    outline = draft_intent([FEATURE], id=INTENT, created_at=AT)["criteria"][1]
    assert outline["key"] == "AC-2"
    assert outline["statement"].endswith(
        "When an amount of <amount> is entered\n"
        "Then the invoice is rejected\n"
        "Examples:\n"
        "| amount |\n"
        "| -1 |\n"
        "| -0.05 |"
    )


def test_a_draft_intent_from_a_feature() -> None:
    intent = draft_intent([FEATURE], id=INTENT, created_at=AT)
    assert intent["title"] == "Invoices round to five Rappen"
    assert intent["status"] == "draft"
    assert intent["version"] == 1
    assert intent["language"] == "en"
    assert [c["key"] for c in intent["criteria"]] == ["AC-1", "AC-2", "AC-3", "AC-4"]
    assert validate(intent).conforms


def test_a_key_used_twice_is_refused() -> None:
    twice = FEATURE.replace("@AC-3", "@AC-1")
    with pytest.raises(CucumberError, match="AC-1"):
        draft_intent([twice], id=INTENT, created_at=AT)


def test_the_key_pattern_can_be_chosen() -> None:
    tagged = FEATURE.replace("@AC-", "@req:AC-")
    intent = draft_intent([tagged], id=INTENT, created_at=AT, key_pattern=r"req:(.+)")
    assert intent["criteria"][0]["key"] == "AC-1"


# --- results -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "result"),
    [
        (["PASSED", "PASSED"], "pass"),
        (["PASSED", "FAILED", "SKIPPED"], "fail"),
        (["PASSED", "UNDEFINED", "SKIPPED"], "error"),
        (["AMBIGUOUS"], "error"),
        (["PASSED", "PENDING"], "skipped"),
        (["SKIPPED"], "skipped"),
    ],
)
def test_step_statuses_to_a_result(statuses: list[str], result: str) -> None:
    assert result_of(statuses) == result


# --- a run to records ---------------------------------------------------------------------------


def run(repo: Path, intent: dict[str, Any], **options: Any) -> Any:
    return record_run(
        MESSAGES, intent=intent, test_producer=QE, namespace=NS, repo=repo, created_at=AT,
        **options,
    )  # fmt: skip


def test_a_run_becomes_evidence_derived_from_the_intent(repo: Path) -> None:
    recorded = run(repo, approved(), subject=f"{NS}/artifacts/invoice-code")
    evidence = by_type(recorded.records, "evidence")
    outcome = sorted((e["criterion"].rsplit("#", 1)[1], e["result"]) for e in evidence)
    assert outcome == [
        ("AC-1", "pass"), ("AC-2", "pass"), ("AC-2", "pass"), ("AC-3", "fail"), ("AC-4", "error"),
    ]  # fmt: skip
    assert {e["derivedFrom"] for e in evidence} == {"intent"}
    assert {e["subject"] for e in evidence} == {f"{NS}/artifacts/invoice-code"}
    [test] = by_type(recorded.records, "artifact")
    assert test["kind"] == "test"
    assert test["path"] == "features/rounding.feature"
    assert test["producedBy"] == QE
    assert test["context"] == [f"{INTENT}@1#AC-{n}" for n in range(1, 5)]
    assert test["contextRecordedBy"] == recorded.adapter
    assert recorded.unmatched == ["features/rounding.feature: Exploratory, no key (no key tag)"]
    for record in recorded.records:
        assert validate(record).conforms, record["id"]


def test_a_scenario_that_differs_from_its_criterion_is_not_derived_from_it(repo: Path) -> None:
    changed = FEATURE.replace("CHF 10.00\n", "CHF 10.00 exactly\n", 1)
    recorded = run(repo, approved(changed))
    ac1 = [e for e in by_type(recorded.records, "evidence") if e["criterion"].endswith("#AC-1")]
    assert [e["derivedFrom"] for e in ac1] == ["unknown"]
    [test] = by_type(recorded.records, "artifact")
    assert f"{INTENT}@1#AC-1" not in test["context"]
    assert any("AC-1" in note and "differs" in note for note in recorded.notes)


def test_a_key_the_intent_lacks_gets_no_evidence(repo: Path) -> None:
    without_ac4 = FEATURE.split("  @AC-4")[0]
    recorded = run(repo, approved(without_ac4))
    keys = {e["criterion"].rsplit("#", 1)[1] for e in by_type(recorded.records, "evidence")}
    assert keys == {"AC-1", "AC-2", "AC-3"}
    assert any("AC-4" in item for item in recorded.unmatched)


def test_the_intent_must_be_approved(repo: Path) -> None:
    draft = draft_intent([FEATURE], id=INTENT, created_at=AT)
    with pytest.raises(CucumberError, match="approve"):
        run(repo, draft)


# --- command line and a passport ------------------------------------------------------------------


def test_cli_from_gherkin_then_cucumber_then_a_passport(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / ".ois"
    common = ["--output-dir", str(out)]
    feature = repo / "features" / "rounding.feature"
    assert ois(["intent", "from-gherkin", str(feature), "--id", INTENT, *common]) == 0
    assert "Exploratory, no key" in capsys.readouterr().err
    draft = out / "intents" / "INT-7-v1.ois.yaml"
    assert draft.exists()
    assert (
        ois(["intent", "approve", str(draft), "--by", PO, "--at", "2026-10-28T09:00:00Z", *common])
        == 0
    )

    at = ["--namespace", NS, "--at", "2026-10-28T10:00:00Z", *common]
    for actor in (
        ["--kind", "human", "--name", "po-7f3a"],
        ["--kind", "human", "--name", "qe-lead"],
        ["--kind", "agent", "--name", "QE agent", "--version", "2.1", "--model", "other-ai:qe:1"],
        ["--kind", "agent", "--name", "Dev agent", "--version", "3.0", "--model", "acme-ai:dev:1"],
        ["--kind", "tool", "--name", "CI", "--version", "7"],
        ["--kind", "tool", "--name", "Issuer", "--version", "1"],
    ):
        assert ois(["record", "actor", *actor, *at]) == 0
    capsys.readouterr()
    assert ois(["record", "artifact", "--commit", str(repo), "--kind", "code",
                "--produced-by", f"{NS}/actors/dev-agent@3.0", *at]) == 0  # fmt: skip
    code = capsys.readouterr().out.strip()
    wheel = tmp_path / "billing.whl"
    wheel.write_bytes(b"wheel")
    build_args = ["record", "artifact", str(wheel), "--kind", "build"]
    build_args += ["--location", "pkg:pypi/billing@1", "--produced-by", f"{NS}/actors/ci@7"]
    assert ois([*build_args, "--input", code, *at]) == 0
    build = capsys.readouterr().out.strip()
    release = ["record", "decision", "--gate", "release", "--subject", build]
    assert ois([*release, "--outcome", "approved", "--by", f"{NS}/actors/qe-lead", *at]) == 0

    args = ["cucumber", str(HERE / "rounding.ndjson"), "--intent", f"{INTENT}@1"]
    args += ["--test-producer", QE, "--subject", code, "--repo", str(repo), "--namespace", NS]
    assert ois([*args, *common]) == 0
    err = capsys.readouterr().err
    assert "5 results: 5 derived from the intent" in err

    passport = tmp_path / "passport.json"
    building = ["passport", "build", str(out), "--release-name", "billing"]
    building += ["--release-version", "1", "--build", build, "--issuer", f"{NS}/actors/issuer@1"]
    assert ois([*building, "--output", str(passport)]) == 0
    statement_ = json.loads(passport.read_text())
    assert validate(statement_).conforms
    measured = {a["name"]: a["value"] for a in statement_["predicate"]["assessments"]}
    assert measured["intentCoverage"] == 0.5  # AC-1 and AC-2 pass; AC-3 fails, AC-4 errs


def test_statement_renders_the_same_from_messages_and_the_feature() -> None:
    document = next(
        json.loads(line)["gherkinDocument"] for line in MESSAGES if "gherkinDocument" in line
    )
    children = document["feature"]["children"]
    background = children[0]["background"]
    scenario = children[1]["scenario"]
    intent = draft_intent([FEATURE], id=INTENT, created_at=AT)
    assert statement(scenario, background) == intent["criteria"][0]["statement"]
