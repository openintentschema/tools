import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ois_validator import validate
from ois_validator.intents import approve
from ois_validator.passport_cli import main as passport_main
from ois_validator.record_cli import main
from ois_validator.records import (
    RecordError,
    actor,
    artifact,
    bundle,
    commit_source,
    decision,
    digest_source,
    evidence,
    file_source,
    finding,
    measurement,
    model,
    reference,
    supersede,
)

NS = "https://ois.acme.example"
AT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
CI = f"{NS}/actors/ci-build@7"
QE = f"{NS}/actors/qe-2c9d"
DEV = f"{NS}/actors/dev-agent@2.1"
ORIGIN = "https://git.acme.example/billing.git"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "invoice.py").write_text("def total():\n    return 10\n")
    git(root, "init", "-q")
    git(root, "remote", "add", "origin", ORIGIN)
    git(root, "add", ".")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init")
    return root


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_artifact(tmp_path: Path) -> dict[str, Any]:
    wheel = tmp_path / "billing-2026.10.4-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    source = file_source(wheel, location="pkg:pypi/billing@2026.10.4")
    return artifact(source, kind="build", produced_by=CI, created_at=AT, namespace=NS)


def violations(document: object) -> set[str]:
    return {r.rule for r in validate(document).reports if r.level == "violation"}


# --- artifacts ------------------------------------------------------------------------------


def test_a_committed_file_is_located_in_git(repo: Path) -> None:
    record = artifact(
        file_source(repo / "src" / "invoice.py"),
        kind="code",
        produced_by=DEV,
        created_at=AT,
        namespace=NS,
    )
    commit = git(repo, "rev-parse", "HEAD")
    digest = sha256(repo / "src" / "invoice.py")
    assert record["location"] == f"git+{ORIGIN}@{commit}"
    assert record["path"] == "src/invoice.py"
    assert record["digest"] == {"sha256": digest, "gitCommit": commit}
    assert record["id"] == f"{NS}/artifacts/invoice-py-{digest[:12]}"
    assert record["createdAt"] == "2026-10-28T12:00:00Z"
    assert validate(record).conforms


def test_a_file_outside_git_needs_a_location(tmp_path: Path) -> None:
    wheel = tmp_path / "app.whl"
    wheel.write_bytes(b"wheel")
    with pytest.raises(RecordError, match="--location"):
        file_source(wheel)
    record = build_artifact(tmp_path)
    assert record["location"] == "pkg:pypi/billing@2026.10.4"
    assert "path" not in record
    assert record["digest"] == {"sha256": hashlib.sha256(b"wheel").hexdigest()}


def test_a_changed_file_is_not_located_at_the_commit(repo: Path) -> None:
    (repo / "src" / "invoice.py").write_text("def total():\n    return 11\n")
    with pytest.raises(RecordError, match="uncommitted"):
        file_source(repo / "src" / "invoice.py")


def test_a_commit_artifact_names_the_commit(repo: Path) -> None:
    commit = git(repo, "rev-parse", "HEAD")
    record = artifact(
        commit_source(repo), kind="code", produced_by=DEV, created_at=AT, namespace=NS
    )
    assert record["digest"] == {"gitCommit": commit}
    assert record["location"] == f"git+{ORIGIN}@{commit}"
    assert record["id"] == f"{NS}/artifacts/billing-{commit[:12]}"  # after origin, not the folder
    assert validate(record).conforms


def test_an_artifact_from_a_known_digest() -> None:
    digest = "4f3c2b1a" * 8
    source = digest_source(f"sha256:{digest}", location="pkg:oci/billing", name="billing image")
    record = artifact(source, kind="build", produced_by=CI, created_at=AT, namespace=NS)
    assert record["digest"] == {"sha256": digest}
    assert record["id"] == f"{NS}/artifacts/billing-image-{digest[:12]}"
    with pytest.raises(RecordError, match="ALGORITHM:HEX"):
        digest_source("4f3c2b1a", location="pkg:oci/billing")


def test_the_same_artifact_gets_the_same_id(tmp_path: Path) -> None:
    first = build_artifact(tmp_path)
    again = artifact(
        file_source(tmp_path / "billing-2026.10.4-py3-none-any.whl", location=first["location"]),
        kind="build",
        produced_by=CI,
        created_at=datetime(2026, 10, 29, tzinfo=UTC),
        namespace=NS,
    )
    assert again["id"] == first["id"]


def test_an_artifact_that_breaks_the_schema_is_refused(tmp_path: Path) -> None:
    data = tmp_path / "customers.csv"
    data.write_text("id\n1\n")
    with pytest.raises(RecordError, match="classification"):
        artifact(
            file_source(data, location="s3://acme/data"),
            kind="dataset",
            produced_by=CI,
            created_at=AT,
            namespace=NS,
        )


def test_an_artifact_needs_an_id_or_a_namespace(tmp_path: Path) -> None:
    source = digest_source("sha256:" + "ab" * 32, location="pkg:oci/billing")
    with pytest.raises(RecordError, match="namespace"):
        artifact(source, kind="build", produced_by=CI, created_at=AT)
    named = artifact(source, kind="build", produced_by=CI, created_at=AT, id=f"{NS}/artifacts/B-1")
    assert named["id"] == f"{NS}/artifacts/B-1"


# --- actors -----------------------------------------------------------------------------------


def test_an_agent_with_its_model() -> None:
    record = actor(
        kind="agent",
        name="Test Agent",
        version="3.2",
        models=[model("acme-ai:coder:2026-09")],
        autonomy_level=1,
        created_at=AT,
        namespace=NS,
    )
    assert record["id"] == f"{NS}/actors/test-agent"
    assert record["model"] == {"provider": "acme-ai", "name": "coder", "version": "2026-09"}
    assert reference(record) == f"{NS}/actors/test-agent@3.2"
    assert validate(record).conforms


def test_a_model_name_may_contain_colons() -> None:
    expected = {"provider": "local", "name": "llama3:8b", "version": "q4"}
    assert model("local:llama3:8b:q4") == expected
    with pytest.raises(RecordError, match="PROVIDER:NAME:VERSION"):
        model("coder")


def test_tools_and_agents_need_a_version() -> None:
    with pytest.raises(RecordError, match="version"):
        actor(kind="tool", name="CI build", created_at=AT, namespace=NS)


def test_a_human_identity_is_not_an_email_address() -> None:
    with pytest.raises(RecordError, match="e-mail"):
        actor(
            kind="human",
            name="po-7f3a",
            identity={"issuer": "https://idp.acme.example", "subject": "anna@acme.example"},
            created_at=AT,
            namespace=NS,
        )
    human = actor(kind="human", name="po-7f3a", created_at=AT, namespace=NS)
    assert reference(human) == f"{NS}/actors/po-7f3a"


# --- evidence ---------------------------------------------------------------------------------


def test_evidence_for_a_criterion() -> None:
    record = evidence(
        criterion=f"{NS}/intents/INT-7@1#AC-2",
        test=f"{NS}/artifacts/test-rounding",
        subject=f"{NS}/artifacts/invoice-py",
        derived_from="intent",
        result="pass",
        recorded_by=CI,
        observed_at=AT,
        measures=[measurement("responseTimeP95=420ms"), measurement("mutationScore=0.82")],
        created_at=AT,
        namespace=NS,
    )
    assert record["id"].startswith(f"{NS}/evidence/AC-2-")
    assert record["measures"] == [
        {"name": "responseTimeP95", "value": 420, "unit": "ms"},
        {"name": "mutationScore", "value": 0.82},
    ]
    assert record["observedAt"] == "2026-10-28T12:00:00Z"
    assert validate(record).conforms


def test_measurements() -> None:
    assert measurement("coverage=87.5%") == {"name": "coverage", "value": 87.5, "unit": "%"}
    assert measurement("flaky=false") == {"name": "flaky", "value": False}
    assert measurement("grade=B") == {"name": "grade", "value": "B"}
    assert measurement("delta=-3") == {"name": "delta", "value": -3}
    with pytest.raises(RecordError, match="NAME=VALUE"):
        measurement("420ms")


# --- decisions --------------------------------------------------------------------------------


def test_a_decision_binds_the_digest_of_its_subject(tmp_path: Path) -> None:
    build = build_artifact(tmp_path)
    record = decision(
        gate="release",
        subject=build["id"],
        outcome="approved",
        decided_by=QE,
        decided_at=AT,
        records=[build],
        namespace=NS,
    )
    assert record["subjectDigest"] == build["digest"]
    assert record["decidedAt"] == record["createdAt"] == "2026-10-28T12:00:00Z"
    assert record["id"].startswith(f"{NS}/decisions/{build['id'].rsplit('/', 1)[1]}-release-")
    assert validate(record).conforms


def test_a_release_without_the_digest_is_refused() -> None:
    with pytest.raises(RecordError, match="subject-digest"):
        decision(
            gate="release",
            subject=f"{NS}/artifacts/unknown",
            outcome="approved",
            decided_by=QE,
            decided_at=AT,
            records=[],
            namespace=NS,
        )
    explicit = decision(
        gate="release",
        subject=f"{NS}/artifacts/unknown",
        subject_digest="sha256:" + "ab" * 32,
        outcome="approved",
        decided_by=QE,
        decided_at=AT,
        records=[],
        namespace=NS,
    )
    assert explicit["subjectDigest"] == {"sha256": "ab" * 32}


def test_a_well_known_gate_checks_subject_and_outcome(tmp_path: Path) -> None:
    build = build_artifact(tmp_path)
    with pytest.raises(RecordError, match=r"merge.*code"):
        decision(
            gate="merge",
            subject=build["id"],
            outcome="passed",
            decided_by=QE,
            decided_at=AT,
            records=[build],
            namespace=NS,
        )
    with pytest.raises(RecordError, match="outcome"):
        decision(
            gate="release",
            subject=build["id"],
            outcome="waived-ish",
            decided_by=QE,
            decided_at=AT,
            records=[build],
            namespace=NS,
        )
    custom = decision(
        gate="security-review",
        subject=build["id"],
        outcome="passed",
        decided_by=QE,
        decided_at=AT,
        records=[build],
        namespace=NS,
    )
    assert custom["gate"] == "security-review"


def test_an_evidence_revocation_is_rejected_and_needs_a_rationale() -> None:
    subject = f"{NS}/evidence/EV-1"
    with pytest.raises(RecordError, match="rationale"):
        decision(
            gate="evidence-revocation",
            subject=subject,
            decided_by=QE,
            decided_at=AT,
            records=[],
            namespace=NS,
        )
    record = decision(
        gate="evidence-revocation",
        subject=subject,
        rationale="flaky test, see PORTAL-1310",
        decided_by=QE,
        decided_at=AT,
        records=[],
        namespace=NS,
    )
    assert record["outcome"] == "rejected"
    assert "subjectDigest" not in record


def test_other_gates_need_an_outcome() -> None:
    with pytest.raises(RecordError, match="outcome"):
        decision(
            gate="merge",
            subject=f"{NS}/artifacts/x",
            decided_by=QE,
            decided_at=AT,
            records=[],
            namespace=NS,
        )


# --- findings ---------------------------------------------------------------------------------


def a_finding(**changes: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "summary": "Rounding is off for negative credit notes",
        "severity": "high",
        "source": "production",
        "reported_by": f"{NS}/actors/support-4b1e",
        "intent": f"{NS}/intents/INT-7@1",
        "observed_at": datetime(2026, 10, 27, 18, tzinfo=UTC),
        "created_at": AT,
        "namespace": NS,
    } | changes
    return finding(**fields)


def test_a_finding() -> None:
    record = a_finding(ticket="https://jira.acme.example/browse/BILL-12")
    assert record["id"].startswith(f"{NS}/findings/F-")
    assert record["observedAt"] == "2026-10-27T18:00:00Z"
    assert validate(record).conforms


def test_a_finding_names_what_it_concerns() -> None:
    with pytest.raises(RecordError, match="criterion, intent or subject"):
        a_finding(intent=None)


def test_superseding_a_finding_continues_the_chain() -> None:
    first = a_finding(id=f"{NS}/findings/FND-18")
    second = supersede(first, records=[first], created_at=AT, cause="implementation")
    assert second["id"] == f"{NS}/findings/FND-18-r2"
    assert second["supersedes"] == first["id"]
    assert second["cause"] == "implementation"
    assert second["observedAt"] == first["observedAt"]
    fix = f"{NS}/evidence/EV-9"
    third = supersede(second, records=[first, second], created_at=AT, resolved_by=[fix])
    assert third["id"] == f"{NS}/findings/FND-18-r3"
    assert third["resolvedBy"] == [fix]
    assert third["cause"] == "implementation"


def test_a_finding_has_at_most_one_successor() -> None:
    first = a_finding(id=f"{NS}/findings/FND-18")
    second = supersede(first, records=[first], created_at=AT, cause="spec-gap")
    with pytest.raises(RecordError, match="R14"):
        supersede(first, records=[first, second], created_at=AT, cause="implementation")


def resolution_records() -> tuple[dict[str, Any], dict[str, Any]]:
    fix = evidence(
        id=f"{NS}/evidence/EV-9",
        criterion=f"{NS}/intents/INT-7@1#AC-2",
        test=f"{NS}/artifacts/test-rounding",
        derived_from="intent",
        result="pass",
        recorded_by=CI,
        observed_at=AT,
        created_at=AT,
    )
    first = a_finding(id=f"{NS}/findings/FND-18")
    resolved = supersede(first, records=[first], created_at=AT, resolved_by=[fix["id"]])
    return fix, resolved


def test_a_finding_resolution_is_made_by_an_independent_actor() -> None:
    fix, resolved = resolution_records()
    first = a_finding(id=f"{NS}/findings/FND-18")
    records = [first, resolved, fix]
    with pytest.raises(RecordError, match="R14"):
        decision(
            gate="finding-resolution",
            subject=resolved["id"],
            outcome="approved",
            decided_by=f"{NS}/actors/ci-build@8",  # another version of the same actor
            decided_at=AT,
            records=records,
            namespace=NS,
        )
    with pytest.raises(RecordError, match="superseded"):
        decision(
            gate="finding-resolution",
            subject=first["id"],
            outcome="approved",
            decided_by=QE,
            decided_at=AT,
            records=records,
            namespace=NS,
        )
    with pytest.raises(RecordError, match="resolvedBy"):
        decision(
            gate="finding-resolution",
            subject=first["id"],
            outcome="approved",
            decided_by=QE,
            decided_at=AT,
            records=[first],
            namespace=NS,
        )
    record = decision(
        gate="finding-resolution",
        subject=resolved["id"],
        outcome="approved",
        decided_by=QE,
        decided_at=AT,
        records=records,
        namespace=NS,
    )
    assert record["subject"] == resolved["id"]


# --- bundles ----------------------------------------------------------------------------------


def test_a_bundle_carries_each_record_once(tmp_path: Path) -> None:
    build = build_artifact(tmp_path)
    ci = actor(kind="tool", name="CI build", version="7", created_at=AT, namespace=NS)
    assert reference(ci) == CI
    made = bundle([build, ci, build], created_at=AT, namespace=NS)
    assert [r["id"] for r in made["records"]] == [build["id"], ci["id"]]
    assert made["id"].startswith(f"{NS}/bundles/")
    assert validate(made).conforms


# --- command line -----------------------------------------------------------------------------


def run(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(args)
    captured = capsys.readouterr()
    return code, captured.out.strip(), captured.err


def test_cli_writes_the_record_and_prints_its_reference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / ".ois"
    common = ["--namespace", NS, "--output-dir", str(out)]
    actor_args = ["actor", "--kind", "tool", "--name", "CI build", "--version", "7", *common]
    code, printed, err = run([*actor_args, "--at", "2026-10-28T12:00:00Z"], capsys)
    assert (code, printed) == (0, CI)
    path = out / "actors" / "ci-build-v7.ois.json"
    assert str(path) in err
    written = json.loads(path.read_text())
    assert written["createdAt"] == "2026-10-28T12:00:00Z"

    # the same actor again: nothing changes, the reference is printed again
    code, printed, err = run([*actor_args, "--at", "2026-10-29T08:00:00Z"], capsys)
    assert (code, printed) == (0, CI)
    assert "unchanged" in err
    assert json.loads(path.read_text()) == written

    # a different actor under the same version: records never change
    code, _, err = run([*actor_args, "--operator", "Platform team"], capsys)
    assert code == 1
    assert "new version" in err


def test_cli_artifact_from_a_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wheel = tmp_path / "billing.whl"
    wheel.write_bytes(b"wheel")
    out = tmp_path / ".ois"
    args = [
        "artifact",
        str(wheel),
        "--kind",
        "build",
        "--location",
        "pkg:pypi/billing@2026.10.4",
        "--produced-by",
        CI,
        "--input",
        f"{NS}/artifacts/repo-0123456789ab",
        "--namespace",
        NS,
        "--output-dir",
        str(out),
    ]
    code, printed, _ = run(args, capsys)
    assert code == 0
    [path] = (out / "artifacts").iterdir()
    record = json.loads(path.read_text())
    assert printed == record["id"]
    assert record["inputs"] == [f"{NS}/artifacts/repo-0123456789ab"]


def test_cli_namespace_from_the_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OIS_NAMESPACE", NS)
    args = ["actor", "--kind", "human", "--name", "po-7f3a", "--output-dir", str(tmp_path)]
    code, printed, _ = run(args, capsys)
    assert (code, printed) == (0, f"{NS}/actors/po-7f3a")


def test_cli_without_namespace_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OIS_NAMESPACE", raising=False)
    args = ["actor", "--kind", "human", "--name", "po-7f3a", "--output-dir", str(tmp_path)]
    code, _, err = run(args, capsys)
    assert code == 1
    assert "OIS_NAMESPACE" in err


def test_cli_decision_reads_the_digest_from_the_output_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / ".ois"
    wheel = tmp_path / "billing.whl"
    wheel.write_bytes(b"wheel")
    common = ["--namespace", NS, "--output-dir", str(out)]
    _, build_id, _ = run(
        [
            "artifact",
            str(wheel),
            "--kind",
            "build",
            "--location",
            "pkg:pypi/billing@1",
            "--produced-by",
            CI,
            *common,
        ],
        capsys,
    )
    code, decision_id, _ = run(
        [
            *["decision", "--gate", "release", "--subject", build_id],
            *["--outcome", "approved", "--by", QE, *common],
        ],
        capsys,
    )
    assert code == 0
    [path] = (out / "decisions").iterdir()
    record = json.loads(path.read_text())
    assert record["id"] == decision_id
    assert record["subjectDigest"] == {"sha256": hashlib.sha256(b"wheel").hexdigest()}


def test_cli_supersede(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / ".ois"
    common = ["--namespace", NS, "--output-dir", str(out)]
    code, finding_id, _ = run(
        [
            *["finding", "--summary", "Rounding is off", "--severity", "high", "--source", "test"],
            *["--reported-by", QE, "--intent", f"{NS}/intents/INT-7@1", *common],
        ],
        capsys,
    )
    assert code == 0
    [first] = (out / "findings").iterdir()
    supersede_args = ["supersede", str(first), "--output-dir", str(out)]
    code, successor, _ = run([*supersede_args, "--cause", "implementation"], capsys)
    assert (code, successor) == (0, f"{finding_id}-r2")
    code, _, err = run([*supersede_args, "--cause", "spec-gap"], capsys)
    assert code == 1
    assert "R14" in err


def test_cli_bundle_refuses_records_that_break_a_rule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lone = a_finding(id=f"{NS}/findings/FND-1")
    source = tmp_path / "finding.json"
    source.write_text(json.dumps(lone))
    out = tmp_path / "bundle.json"
    code, _, err = run(["bundle", str(source), "--namespace", NS, "--output", str(out)], capsys)
    assert code == 1
    assert "R10" in err
    assert not out.exists()


def test_a_passport_from_recorded_records_only(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every record of a release can be written with the commands, none by hand."""
    out = tmp_path / ".ois"
    common = ["--namespace", NS, "--output-dir", str(out)]
    at = ["--at", "2026-10-28T12:00:00Z"]

    def record(*args: str) -> str:
        code, printed, err = run([*args, *common, *at], capsys)
        assert code == 0, err
        return printed

    po = record("actor", "--kind", "human", "--name", "po-7f3a")
    qe = record("actor", "--kind", "human", "--name", "qe-2c9d")
    dev = record(
        "actor", "--kind", "agent", "--name", "Dev Agent", "--version", "2.1",
        "--model", "acme-ai:coder:2026-09",
    )  # fmt: skip
    tester = record(
        "actor", "--kind", "agent", "--name", "Test Agent", "--version", "1.0",
        "--model", "acme-ai:tester:2026-09",
    )  # fmt: skip
    ci = record("actor", "--kind", "tool", "--name", "CI build", "--version", "7")
    issuer = record("actor", "--kind", "tool", "--name", "Passport issuer", "--version", "1")

    intent = {
        "id": f"{NS}/intents/INT-7",
        "createdAt": "2026-10-28T09:00:00Z",
        "version": 1,
        "title": "Invoices round to five Rappen",
        "status": "draft",
        "criteria": [
            {
                "key": "AC-1",
                "notation": "text",
                "kind": "functional",
                "statement": "Totals round to five Rappen",
            }
        ],
    }
    approved, approval = approve(intent, decided_by=po, decided_at=AT)
    for written in (approved, approval):
        path = out / f"{written['type']}s" / "INT-7.ois.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(written))

    code_artifact = record(
        "artifact", str(repo / "src" / "invoice.py"), "--kind", "code", "--produced-by", dev
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_invoice.py").write_text("def test_rounding():\n    pass\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "test")
    test_artifact = record(
        "artifact", str(repo / "tests" / "test_invoice.py"), "--kind", "test",
        "--produced-by", tester, "--context", f"{NS}/intents/INT-7@1#AC-1",
    )  # fmt: skip
    record(
        "evidence", "--criterion", f"{NS}/intents/INT-7@1#AC-1", "--test", test_artifact,
        "--subject", code_artifact, "--derived-from", "intent", "--result", "pass",
        "--recorded-by", ci,
    )  # fmt: skip
    wheel = tmp_path / "billing.whl"
    wheel.write_bytes(b"wheel")
    build = record(
        "artifact", str(wheel), "--kind", "build", "--location", "pkg:pypi/billing@2026.10.4",
        "--produced-by", ci, "--input", code_artifact,
    )  # fmt: skip
    record("decision", "--gate", "release", "--subject", build, "--outcome", "approved", "--by", qe)

    passport = tmp_path / "passport.json"
    code = passport_main(
        [
            *["build", str(out), "--release-name", "billing", "--release-version", "2026.10.4"],
            *["--build", build, "--issuer", issuer, "--at", "2026-10-28T13:00:00Z"],
            *["--output", str(passport)],
        ]
    )
    assert code == 0, capsys.readouterr().err
    result = validate(json.loads(passport.read_text()))
    assert result.conforms
    assert {r.rule for r in result.reports} == {"R12"}  # the test's context is self-declared

    bundle_path = tmp_path / "bundle.json"
    code, _, _ = run(["bundle", str(out), "--namespace", NS, "--output", str(bundle_path)], capsys)
    assert code == 0
    assert len(json.loads(bundle_path.read_text())["records"]) == 13
