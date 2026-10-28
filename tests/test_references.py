import json
from pathlib import Path
from typing import Any

from ois_validator import Level, validate

FIXTURES = Path(__file__).parent / "fixtures"


def load(path: str) -> Any:
    return json.loads((FIXTURES / path).read_text(encoding="utf-8"))


def r10(document: object) -> list[tuple[str, str]]:
    return [(r.path, r.message) for r in validate(document).reports if r.rule == "R10"]


def test_unresolved_reference_points_at_the_field() -> None:
    bundle = load("valid/bundle.json")
    index = next(i for i, r in enumerate(bundle["records"]) if r["type"] == "evidence")
    bundle["records"][index]["inputs"].append("https://ois.acme.example/artifacts/ART-999")
    [(path, message)] = r10(bundle)
    assert path == f"$.records[{index}].inputs[1]"
    assert "ART-999" in message


def test_conflict_names_both_records() -> None:
    [(path, message)] = r10(load("invalid/R10/conflicting-records.json"))
    assert path == "$.records[11]"
    assert "$.records[10]" in message


def test_passport_level_references_are_resolved() -> None:
    passport = load("valid/passport.json")
    passport["predicate"]["issuedBy"] = "https://ois.acme.example/actors/passport-issuer@2.0"
    assert [path for path, _ in r10(passport)] == ["$.predicate.issuedBy"]


def test_single_record_is_not_checked_for_resolution() -> None:
    assert r10(load("valid/finding.json")) == []


def test_r10_is_a_violation() -> None:
    [report] = [
        r
        for r in validate(load("invalid/R10/unresolved-reference.json")).reports
        if r.rule == "R10"
    ]
    assert report.level is Level.VIOLATION
