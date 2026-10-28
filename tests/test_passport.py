import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ois_validator import validate
from ois_validator.intents import approve, next_version
from ois_validator.passport_cli import main
from ois_validator.passports import PassportError, Release, build, collect

FIXTURES = Path(__file__).parent / "fixtures" / "valid"
NS = "https://ois.acme.example"
BUILD = f"{NS}/artifacts/ART-305"
ISSUER = f"{NS}/actors/passport-issuer@1.0"
QE_LEAD = f"{NS}/actors/qe-2c9d"
RELEASE_DECISION = f"{NS}/decisions/DEC-81"
AT = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
RELEASE = Release(name="customer-portal", version="2026.10.3")


def fixture_records() -> list[dict[str, Any]]:
    passport = json.loads((FIXTURES / "passport.json").read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = passport["predicate"]["records"]
    return records


def without(records: list[dict[str, Any]], record_id: str) -> list[dict[str, Any]]:
    return [r for r in records if r["id"] != record_id]


def rules(document: object) -> set[tuple[str, str]]:
    return {(r.rule, str(r.level)) for r in validate(document).reports}


# --- collect ------------------------------------------------------------------------------


def test_collect_flattens_bundles_and_merges_identical_records() -> None:
    records = fixture_records()
    bundle = {
        "ois": "0.1",
        "type": "bundle",
        "id": f"{NS}/bundles/B-1",
        "createdAt": "2026-10-20T15:00:00Z",
        "records": records[:5],
    }
    collected = collect([bundle, *records])
    assert [r["id"] for r in collected] == [r["id"] for r in records]


def test_collect_keeps_the_approved_state_of_an_intent() -> None:
    records = fixture_records()
    intent = next(r for r in records if r["type"] == "intent")
    draft = {**copy.deepcopy(intent), "status": "draft"}
    collected = collect([draft, *records])
    [kept] = [r for r in collected if r["type"] == "intent"]
    assert kept["status"] == "approved"


def test_collect_refuses_conflicting_records() -> None:
    records = fixture_records()
    changed = {**copy.deepcopy(records[8]), "path": "somewhere/else.yaml"}
    with pytest.raises(PassportError, match="R10"):
        collect([*records, changed])


# --- build --------------------------------------------------------------------------------


def test_build_reproduces_the_conformance_fixture() -> None:
    passport = build(
        collect(fixture_records()), release=RELEASE, build=BUILD, issued_by=ISSUER, issued_at=AT
    )
    assert passport["_type"] == "https://in-toto.io/Statement/v1"
    assert passport["subject"] == [
        {
            "name": "customer-portal",
            "digest": next(r for r in fixture_records() if r["id"] == BUILD)["digest"],
        }
    ]
    assert passport["predicate"]["intents"] == [f"{NS}/intents/INT-142@1"]
    assert passport["predicate"]["issuedAt"] == "2026-10-21T09:00:00Z"
    assert rules(passport) == {("R12", "warning")}


def test_build_creates_the_release_decision_when_asked() -> None:
    records = without(fixture_records(), RELEASE_DECISION)
    passport = build(
        collect(records),
        release=RELEASE,
        build=BUILD,
        issued_by=ISSUER,
        issued_at=AT,
        approve_release_by=QE_LEAD,
    )
    [decision] = [r for r in passport["predicate"]["records"] if r.get("gate") == "release"]
    assert decision["subject"] == BUILD
    assert decision["outcome"] == "approved"
    assert decision["decidedBy"] == QE_LEAD
    assert decision["id"] == f"{NS}/decisions/ART-305-release"
    assert rules(passport) == {("R12", "warning")}


def test_build_without_a_release_decision_breaks_r16() -> None:
    records = without(fixture_records(), RELEASE_DECISION)
    with pytest.raises(PassportError, match="R16"):
        build(collect(records), release=RELEASE, build=BUILD, issued_by=ISSUER, issued_at=AT)


def test_build_needs_the_build_artifact() -> None:
    with pytest.raises(PassportError, match="build"):
        build(
            collect(without(fixture_records(), BUILD)),
            release=RELEASE,
            build=BUILD,
            issued_by=ISSUER,
            issued_at=AT,
        )


def test_build_lists_the_latest_approved_version_of_each_intent() -> None:
    records = collect(fixture_records())
    v1 = next(r for r in records if r["type"] == "intent")
    v2 = next_version(v1, created_at=datetime(2026, 10, 19, tzinfo=UTC))
    approved_v2, approval = approve(
        v2, decided_by=f"{NS}/actors/po-7f3a", decided_at=datetime(2026, 10, 19, 1, tzinfo=UTC)
    )
    passport = build(
        collect([*records, approved_v2, approval]),
        release=RELEASE,
        build=BUILD,
        issued_by=ISSUER,
        issued_at=AT,
    )
    assert passport["predicate"]["intents"] == [f"{NS}/intents/INT-142@2"]


def test_build_with_explicit_intents_and_scope() -> None:
    passport = build(
        collect(fixture_records()),
        release=RELEASE,
        build=BUILD,
        issued_by=ISSUER,
        issued_at=AT,
        intents=[f"{NS}/intents/INT-142@1"],
        scope=[f"{NS}/intents/INT-142@1#AC-2"],
    )
    assert passport["predicate"]["scope"] == [f"{NS}/intents/INT-142@1#AC-2"]


def test_build_refuses_a_passport_that_would_not_conform() -> None:
    with pytest.raises(PassportError, match="R10"):
        build(
            collect(fixture_records()),
            release=RELEASE,
            build=BUILD,
            issued_by=ISSUER,
            issued_at=AT,
            intents=[f"{NS}/intents/INT-142@9"],
        )


# --- command line ---------------------------------------------------------------------------


def test_cli_builds_from_a_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    records = tmp_path / ".ois"
    for index, record in enumerate(fixture_records()):
        target = records / record["type"] / f"{index}.ois.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record), encoding="utf-8")
    out = tmp_path / "passport.json"
    args = [
        "build",
        str(records),
        "--release-name",
        "customer-portal",
        "--release-version",
        "2026.10.3",
        "--build",
        BUILD,
        "--issuer",
        ISSUER,
        "--at",
        "2026-10-21T09:00:00Z",
        "--output",
        str(out),
    ]
    assert main(args) == 0
    assert validate(json.loads(out.read_text())).conforms
    assert "R12" in capsys.readouterr().err


def test_cli_writes_nothing_when_the_passport_breaks_a_rule(tmp_path: Path) -> None:
    source = tmp_path / "records.json"
    bundle = {
        "ois": "0.1",
        "type": "bundle",
        "id": f"{NS}/bundles/B-1",
        "createdAt": "2026-10-20T15:00:00Z",
        "records": without(fixture_records(), RELEASE_DECISION),
    }
    source.write_text(json.dumps(bundle), encoding="utf-8")
    out = tmp_path / "passport.json"
    args = [
        "build",
        str(source),
        "--release-name",
        "customer-portal",
        "--release-version",
        "1",
        "--build",
        BUILD,
        "--issuer",
        ISSUER,
        "--output",
        str(out),
    ]
    assert main(args) == 1
    assert not out.exists()
