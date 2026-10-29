"""Gherkin and Cucumber runs. Real runs of rounding.feature (AC-1, AC-2 pass; AC-3 fails; AC-4
undefined; one scenario without key): rounding.ndjson from cucumber-js 13.3.0; cucumber-jvm 8.0.4
messages and JSON; JSON of cucumber-js 13.3.0, Godog 0.16.0, pytest-bdd 9.0.0 and behave 1.3.3
(error texts shortened)."""

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
    return git_repo(tmp_path, "features/rounding.feature")


def git_repo(tmp_path: Path, *features: str) -> Path:
    root = tmp_path / "billing"
    for feature in features:
        (root / feature).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(HERE / "rounding.feature", root / feature)

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


def run(repo: Path, intent: dict[str, Any], report: str = "rounding.ndjson", **options: Any) -> Any:
    text = (HERE / report).read_text(encoding="utf-8")
    return record_run(
        text, intent=intent, test_producer=QE, namespace=NS, repo=repo, created_at=AT, **options
    )


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


def test_a_context_recorder_that_signs_makes_the_context_attested(repo: Path) -> None:
    from ois_tools import records, signing

    private_pem, public_pem, keyid = signing.generate_key()
    pipeline = records.actor(kind="tool", name="Release pipeline", version="1", created_at=AT,
                             namespace=NS, identity={"keyId": keyid})  # fmt: skip
    recorder = records.reference(pipeline)
    recorded = run(repo, approved(), context_recorded_by=recorder)
    [test] = by_type(recorded.records, "artifact")
    assert test["contextRecordedBy"] == recorder
    assert {e["recordedBy"] for e in by_type(recorded.records, "evidence")} == {recorded.adapter}
    bundle = {"ois": "0.1", "type": "bundle", "id": f"{NS}/bundles/b", "createdAt": AT.isoformat(),
              "records": [approved(), pipeline, *recorded.records]}  # fmt: skip
    unsigned = validate(bundle)
    assert any(r.rule == "R12" for r in unsigned.reports)
    envelope = signing.sign(bundle, signing.load_private_key(private_pem))
    signed = validate(envelope, trusted_keys=[signing.load_public_key(public_pem)])
    assert not any(r.rule == "R12" for r in signed.reports)


def test_cli_names_a_context_recorder(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "ois"
    (out / "intents").mkdir(parents=True)
    (out / "intents" / "INT-7-v1.ois.json").write_text(json.dumps(approved()), encoding="utf-8")
    messages = tmp_path / "run.ndjson"
    messages.write_text("\n".join(MESSAGES), encoding="utf-8")
    recorder = f"{NS}/actors/release-pipeline@1"
    args = ["cucumber", str(messages), "--intent", f"{INTENT}@1", "--test-producer", QE]
    args += ["--namespace", NS, "--repo", str(repo), "--output-dir", str(out)]
    assert ois([*args, "--context-recorded-by", recorder]) == 0
    [test] = [json.loads(p.read_text()) for p in (out / "artifacts").iterdir()]
    assert test["contextRecordedBy"] == recorder


# --- other runners and formats --------------------------------------------------------------------

JVM_FEATURE = "src/test/resources/features/rounding.feature"


@pytest.mark.parametrize(
    ("report", "feature"),
    [
        ("cucumber-jvm.ndjson", JVM_FEATURE),
        ("cucumber-jvm.json", JVM_FEATURE),
        ("cucumber-js.json", "features/rounding.feature"),
        ("godog.json", "features/rounding.feature"),
        ("pytest-bdd.json", "features/rounding.feature"),
        ("behave.json", "features/rounding.feature"),
    ],
)
def test_each_runner_gives_evidence_derived_from_the_intent(
    tmp_path: Path, report: str, feature: str
) -> None:
    recorded = run(git_repo(tmp_path, feature), approved(), report, reported_at=AT)
    evidence = by_type(recorded.records, "evidence")
    outcome = sorted((e["criterion"].rsplit("#", 1)[1], e["result"]) for e in evidence)
    assert outcome == [
        ("AC-1", "pass"), ("AC-2", "pass"), ("AC-2", "pass"), ("AC-3", "fail"), ("AC-4", "error"),
    ]  # fmt: skip
    assert {e["derivedFrom"] for e in evidence} == {"intent"}
    [test] = by_type(recorded.records, "artifact")
    assert test["path"] == feature
    assert test["context"] == [f"{INTENT}@1#AC-{n}" for n in range(1, 5)]
    assert recorded.unmatched == [f"{feature}: Exploratory, no key (no key tag)"]
    for record in recorded.records:
        assert validate(record).conforms, record["id"]


def test_a_json_report_is_observed_when_it_was_written(tmp_path: Path) -> None:
    written = datetime(2026, 10, 29, 7, 30, tzinfo=UTC)
    repo = git_repo(tmp_path, "features/rounding.feature")
    recorded = run(repo, approved(), "godog.json", reported_at=written)
    assert {e["observedAt"] for e in by_type(recorded.records, "evidence")} == {
        "2026-10-29T07:30:00Z"
    }


def test_a_json_report_scenario_that_differs_is_not_derived(tmp_path: Path) -> None:
    repo = git_repo(tmp_path, "features/rounding.feature")
    changed = FEATURE.replace("CHF 10.00\n", "CHF 10.00 exactly\n", 1)
    recorded = run(repo, approved(changed), "pytest-bdd.json", reported_at=AT)
    ac1 = [e for e in by_type(recorded.records, "evidence") if e["criterion"].endswith("#AC-1")]
    assert [e["derivedFrom"] for e in ac1] == ["unknown"]


@pytest.mark.parametrize(
    ("features", "message"),
    [
        ((), "classpath:features/rounding.feature matches no file"),
        ((JVM_FEATURE, "src/main/resources/features/rounding.feature"), "matches several files"),
    ],
)
def test_a_classpath_feature_must_be_one_file_in_git(
    tmp_path: Path, features: tuple[str, ...], message: str
) -> None:
    repo = git_repo(tmp_path, "other/x.feature", *features)
    with pytest.raises(CucumberError, match=message):
        run(repo, approved(), "cucumber-jvm.json", reported_at=AT)


def test_a_json_report_needs_its_feature_files(tmp_path: Path) -> None:
    repo = git_repo(tmp_path, "other/rounding.feature")
    with pytest.raises(CucumberError, match=r"features/rounding\.feature: feature file not found"):
        run(repo, approved(), "behave.json", reported_at=AT)


def test_an_unknown_report_is_refused(repo: Path, tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text('{"suites": []}', encoding="utf-8")
    with pytest.raises(CucumberError, match="not Cucumber Messages, Cucumber JSON or behave JSON"):
        record_run((tmp_path / "x.json").read_text(), intent=approved(), test_producer=QE,
                   namespace=NS, repo=repo, created_at=AT)  # fmt: skip


def test_cli_records_a_json_report_at_its_modification_time(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import os

    repo = git_repo(tmp_path, "features/rounding.feature")
    out = tmp_path / "ois"
    (out / "intents").mkdir(parents=True)
    (out / "intents" / "INT-7-v1.ois.json").write_text(json.dumps(approved()), encoding="utf-8")
    report = tmp_path / "cucumber.json"
    shutil.copy(HERE / "pytest-bdd.json", report)
    written = datetime(2026, 10, 29, 7, 30, tzinfo=UTC).timestamp()
    os.utime(report, (written, written))
    args = ["cucumber", str(report), "--intent", f"{INTENT}@1", "--test-producer", QE]
    assert ois([*args, "--namespace", NS, "--repo", str(repo), "--output-dir", str(out)]) == 0
    assert "5 results: 5 derived from the intent, 0 not" in capsys.readouterr().err
    observed = {json.loads(p.read_text())["observedAt"] for p in (out / "evidence").iterdir()}
    assert observed == {"2026-10-29T07:30:00Z"}


def changed_report(tmp_path: Path, report: str, change: Any) -> str:
    data = json.loads((HERE / report).read_text(encoding="utf-8"))
    change(next(e for e in data[0]["elements"] if e["name"] == "Round down"))
    (tmp_path / report).write_text(json.dumps(data), encoding="utf-8")
    return str(tmp_path / report)


def ac1_results(tmp_path: Path, feature: str, report: str) -> list[str]:
    recorded = run(git_repo(tmp_path, feature), approved(), report, reported_at=AT)
    evidence = by_type(recorded.records, "evidence")
    return [e["result"] for e in evidence if e["criterion"].endswith("#AC-1")]


def test_a_failed_hook_fails_the_scenario(tmp_path: Path) -> None:
    def hook(element: dict[str, Any]) -> None:
        element["after"] = [{"match": {"location": "Hooks.close"}, "result": {"status": "failed"}}]

    report = changed_report(tmp_path, "cucumber-jvm.json", hook)
    assert ac1_results(tmp_path, JVM_FEATURE, report) == ["fail"]


def test_a_scenario_behave_skips_while_running_is_skipped(tmp_path: Path) -> None:
    def skip(element: dict[str, Any]) -> None:
        element["status"] = "skipped"
        del element["steps"][-1]["result"]

    report = changed_report(tmp_path, "behave.json", skip)
    assert ac1_results(tmp_path, "features/rounding.feature", report) == ["skipped"]


def test_messages_without_a_scenario_hold_no_execution(repo: Path) -> None:
    meta = (HERE / "rounding.ndjson").read_text(encoding="utf-8").splitlines()[0]
    with pytest.raises(CucumberError, match="no executed scenario"):
        record_run(meta, intent=approved(), test_producer=QE, namespace=NS, repo=repo,
                   created_at=AT)  # fmt: skip
