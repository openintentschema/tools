import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ois_validator import digests, validate
from ois_validator.authoring import AuthoringError, loads_yaml
from ois_validator.intent_cli import main
from ois_validator.intents import IntentError, approve, compare, next_version, seal

NS = "https://ois.acme.example"
PO = f"{NS}/actors/po-7f3a"
AT = datetime(2026, 10, 14, 10, 5, tzinfo=UTC)

INTENT_YAML = """\
id: https://ois.acme.example/intents/INT-142
createdAt: 2026-10-14T09:12:00Z
version: 1
title: Customers change their address in the portal
status: draft
language: en
producedBy: https://ois.acme.example/actors/requirement-agent@1.4
risk: high
criteria:
  - key: AC-1
    notation: gherkin
    kind: functional
    criticality: high
    statement: >-
      Given a logged-in customer, when they submit a valid Swiss address,
      then the new address is saved with the chosen effective date
  - key: NFR-1
    notation: ears
    kind: non-functional
    statement: THE portal SHALL confirm an address change within 500 milliseconds
    target: {metric: responseTimeP95, operator: lte, value: 500, unit: ms}
"""


def intent() -> Any:
    return loads_yaml(INTENT_YAML)


# --- YAML as section 7 allows it --------------------------------------------------------


def test_yaml_keeps_json_types() -> None:
    loaded = intent()
    assert loaded["createdAt"] == "2026-10-14T09:12:00Z"
    assert loaded["version"] == 1
    assert loaded["criteria"][1]["target"]["value"] == 500
    assert loads_yaml("n: 0123") == {"n": 123}  # decimal, not YAML 1.1 octal
    assert loads_yaml("a: yes\nb: on\nc: 1.5\nd: null\ne: true") == {
        "a": "yes",
        "b": "on",
        "c": 1.5,
        "d": None,
        "e": True,
    }


@pytest.mark.parametrize(
    "text",
    ["a: !!binary aGVsbG8=", "a: !custom x", "base: &b {x: 1}\nother:\n  <<: *b", "1: one"],
    ids=["standard tag", "custom tag", "merge key", "non-string key"],
)
def test_yaml_without_a_json_equivalent_is_refused(text: str) -> None:
    with pytest.raises(AuthoringError):
        loads_yaml(text)


# --- seal -----------------------------------------------------------------------------------


def test_seal_computes_section_6_4_digests() -> None:
    sealed = seal(intent())
    for criterion in sealed["criteria"]:
        assert criterion["digest"] == {"sha256": digests.criterion_sha256(criterion)}
    assert sealed["digest"] == {"sha256": digests.intent_sha256(sealed)}
    assert validate(sealed).reports == []


def test_seal_adds_the_constant_fields() -> None:
    sealed = seal(intent())
    assert (sealed["ois"], sealed["type"]) == ("0.1", "intent")


def test_seal_does_not_change_its_input() -> None:
    source = intent()
    seal(source)
    assert "digest" not in source


# --- approve ----------------------------------------------------------------------------------


def test_approve_binds_the_decision_to_the_digest() -> None:
    approved, decision = approve(intent(), decided_by=PO, decided_at=AT)
    assert approved["status"] == "approved"
    assert decision["gate"] == "intent-approval"
    assert decision["outcome"] == "approved"
    assert decision["subject"] == f"{NS}/intents/INT-142@1"
    assert decision["subjectDigest"] == approved["digest"]
    assert decision["decidedBy"] == PO
    assert decision["decidedAt"] == decision["createdAt"] == "2026-10-14T10:05:00Z"
    assert decision["id"] == f"{NS}/decisions/INT-142-v1-approval"


def test_approved_records_pass_r1_and_r17_in_a_bundle() -> None:
    approved, decision = approve(intent(), decided_by=PO, decided_at=AT)
    actors = [
        {
            "ois": "0.1",
            "type": "actor",
            "id": PO,
            "createdAt": "2026-09-01T08:00:00Z",
            "kind": "human",
            "name": "PO 7f3a",
        },
        {
            "ois": "0.1",
            "type": "actor",
            "id": f"{NS}/actors/requirement-agent",
            "createdAt": "2026-09-01T08:00:00Z",
            "kind": "agent",
            "name": "Requirement agent",
            "version": "1.4",
            "model": {"provider": "ExampleAI", "name": "model-a", "version": "2026-09"},
        },
    ]
    bundle = {
        "ois": "0.1",
        "type": "bundle",
        "id": f"{NS}/bundles/B-1",
        "createdAt": "2026-10-14T11:00:00Z",
        "records": [*actors, approved, decision],
    }
    assert validate(bundle).reports == []


def test_approve_keeps_an_unchanged_approved_intent() -> None:
    approved, _ = approve(intent(), decided_by=PO, decided_at=AT)
    again, _ = approve(approved, decided_by=PO, decided_at=AT)
    assert again["digest"] == approved["digest"]


def test_approve_refuses_an_approved_intent_that_was_changed() -> None:
    approved, _ = approve(intent(), decided_by=PO, decided_at=AT)
    approved["title"] = "Customers change their address and phone number"
    with pytest.raises(IntentError, match="next version"):
        approve(approved, decided_by=PO, decided_at=AT)


def test_approve_refuses_a_withdrawn_intent() -> None:
    withdrawn = {**intent(), "status": "withdrawn"}
    with pytest.raises(IntentError, match="withdrawn"):
        approve(withdrawn, decided_by=PO, decided_at=AT)


def test_approve_with_policy_and_explicit_decision_id() -> None:
    _, decision = approve(
        intent(),
        decided_by=PO,
        decided_at=AT,
        decision_id=f"{NS}/decisions/DEC-77",
        policy=f"{NS}/policies/definition-of-ready",
        policy_version="4",
    )
    assert decision["id"] == f"{NS}/decisions/DEC-77"
    assert decision["policy"] == {"id": f"{NS}/policies/definition-of-ready", "version": "4"}


# --- next version and compare -------------------------------------------------------------


def test_next_version_supersedes_the_previous_one() -> None:
    approved, _ = approve(intent(), decided_by=PO, decided_at=AT)
    following = next_version(approved, created_at=AT)
    assert following["version"] == 2
    assert following["status"] == "draft"
    assert following["supersedes"] == f"{NS}/intents/INT-142@1"
    assert following["createdAt"] == "2026-10-14T10:05:00Z"
    assert "digest" not in following
    assert all("digest" not in c for c in following["criteria"])


def test_compare_tells_which_evidence_carries_over() -> None:
    old = seal(intent())
    new = next_version(old, created_at=AT)
    new["criteria"][1]["statement"] += " for 99 percent of requests"
    new["criteria"].append(
        {
            "key": "AC-3",
            "notation": "text",
            "kind": "functional",
            "statement": "The customer receives a confirmation",
        }
    )
    old["criteria"].append(
        {"key": "AC-0", "notation": "text", "kind": "functional", "statement": "Dropped"}
    )
    assert compare(old, new) == [
        ("AC-1", "unchanged"),
        ("NFR-1", "changed"),
        ("AC-0", "removed"),
        ("AC-3", "added"),
    ]


def test_criticality_does_not_make_evidence_stale() -> None:
    old = intent()
    new = next_version(old, created_at=AT)
    new["criteria"][0]["criticality"] = "low"
    assert compare(old, new)[0] == ("AC-1", "unchanged")


# --- command line ---------------------------------------------------------------------------


def write(tmp_path: Path, text: str = INTENT_YAML) -> Path:
    path = tmp_path / "INT-142.ois.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_cli_seal_prints_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["seal", str(write(tmp_path))]) == 0
    assert "digest" in json.loads(capsys.readouterr().out)


def test_cli_approve_writes_the_repository_layout(tmp_path: Path) -> None:
    out = tmp_path / ".ois"
    args = [
        "approve",
        str(write(tmp_path)),
        "--by",
        PO,
        "--at",
        "2026-10-14T10:05:00Z",
        "--output-dir",
        str(out),
    ]
    assert main(args) == 0
    approved = json.loads((out / "intents" / "INT-142-v1.ois.json").read_text())
    decision = json.loads((out / "decisions" / "INT-142-v1-approval.ois.json").read_text())
    assert decision["subjectDigest"] == approved["digest"]


def test_cli_next_writes_yaml(tmp_path: Path) -> None:
    out = tmp_path / ".ois"
    assert main(["next", str(write(tmp_path)), "--output-dir", str(out)]) == 0
    following = loads_yaml((out / "intents" / "INT-142-v2.ois.yaml").read_text())
    assert isinstance(following, dict)
    assert following["version"] == 2


def test_cli_compare(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    old = write(tmp_path)
    new = tmp_path / "v2.ois.yaml"
    new.write_text(
        INTENT_YAML.replace("version: 1", "version: 2").replace(
            "500 milliseconds", "300 milliseconds"
        )
    )
    assert main(["compare", str(old), str(new)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "AC-1: unchanged, evidence carries over",
        "NFR-1: changed, evidence is stale",
    ]


def test_cli_reports_authoring_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["seal", str(write(tmp_path, "id: !custom x"))]) == 2
    assert "tag" in capsys.readouterr().err
