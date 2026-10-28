import json
from pathlib import Path
from typing import Any

import pytest

from ois_tools import Level, validate

EXAMPLES = Path(__file__).parent / "examples"


def load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def problems(document: object) -> list[tuple[str, str]]:
    return [(r.path, r.message) for r in validate(document).reports]


@pytest.mark.parametrize("name", ["bundle.json", "passport.json", "finding.json"])
def test_examples_pass_the_schema(name: str) -> None:
    # Appendix A is not self-contained on purpose, so only the schema check applies.
    assert [r for r in validate(load(name)).reports if r.rule == "schema"] == []


def test_reports_carry_level_and_rule() -> None:
    finding = load("finding.json")
    del finding["severity"]
    [report] = validate(finding).reports
    assert report.level is Level.VIOLATION
    assert report.rule == "schema"
    assert report.path == "$"
    assert "'severity' is a required property" in report.message


def test_unknown_record_type() -> None:
    assert problems({"type": "requirement"}) == [
        ("$.type", "type must be one of intent, artifact, actor, evidence, decision, finding")
    ]


def test_non_object_document() -> None:
    assert [path for path, _ in problems([])] == ["$.type"]


def test_record_in_bundle_reports_the_failing_field_not_one_of() -> None:
    bundle = load("bundle.json")
    intent = next(r for r in bundle["records"] if r["type"] == "intent")
    index = bundle["records"].index(intent)
    intent["criteria"][0]["notation"] = "yaml"
    assert problems(bundle) == [
        (
            f"$.records[{index}].criteria[0].notation",
            "'yaml' is not one of ['gherkin', 'ears', 'text']",
        )
    ]


def test_formats_are_asserted() -> None:
    finding = load("finding.json")
    finding["createdAt"] = "yesterday"
    assert problems(finding) == [("$.createdAt", "'yesterday' is not a 'date-time'")]


def test_failing_common_field_does_not_flag_other_common_fields() -> None:
    finding = load("finding.json")
    finding["ois"] = "0.2"
    assert [path for path, _ in problems(finding)] == ["$.ois"]


def test_unknown_property_is_reported_next_to_other_errors() -> None:
    finding = load("finding.json")
    finding["ois"] = "0.2"
    finding["score"] = 7
    assert problems(finding) == [
        ("$", "Unevaluated properties are not allowed ('score' unexpected)"),
        ("$.ois", "'0.1' was expected"),
    ]


def test_unknown_property_on_bundle() -> None:
    bundle = load("bundle.json")
    bundle["signature"] = "abc"
    assert [path for path, _ in problems(bundle)] == ["$"]


def test_record_of_wrong_type_in_bundle() -> None:
    bundle = load("bundle.json")
    bundle["records"].append({"type": "bundle"})
    index = len(bundle["records"]) - 1
    assert [path for path, _ in problems(bundle)] == [f"$.records[{index}].type"]


def test_passport_predicate_is_checked() -> None:
    passport = load("passport.json")
    passport["predicate"]["intents"] = []
    assert [path for path, _ in problems(passport)] == ["$.predicate.intents"]


def test_passport_records_are_checked_one_by_one() -> None:
    passport = load("passport.json")
    del passport["predicate"]["records"][0]["id"]
    assert problems(passport) == [("$.predicate.records[0]", "'id' is a required property")]


def test_passport_with_other_predicate_type() -> None:
    passport = load("passport.json")
    passport["predicateType"] = "https://slsa.dev/provenance/v1"
    assert [path for path, _ in problems(passport)] == ["$.predicateType"]
