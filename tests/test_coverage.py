import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ois_tools import digests
from ois_tools.coverage import METHOD, CoverageError, coverage, passport_coverage
from ois_tools.coverage_cli import main
from ois_tools.intents import approve, next_version
from ois_tools.passport_cli import main as passport_main
from ois_tools.passports import Release, build, collect

FIXTURES = Path(__file__).parent / "fixtures" / "valid"
NS = "https://ois.acme.example"
INTENT = f"{NS}/intents/INT-142@1"
BUILD = f"{NS}/artifacts/ART-305"
CODE = f"{NS}/artifacts/ART-302"
EVIDENCE = f"{NS}/evidence/EV-9021"

Record = dict[str, Any]


def passport() -> Record:
    loaded: Record = json.loads((FIXTURES / "passport.json").read_text(encoding="utf-8"))
    return loaded


def records() -> list[Record]:
    found: list[Record] = passport()["predicate"]["records"]
    return found


def by_id(items: list[Record], record_id: str) -> Record:
    return next(r for r in items if r["id"] == record_id)


def values(items: list[Record], **options: Any) -> dict[str, float]:
    fields: dict[str, Any] = {"subject": BUILD, "intents": [INTENT]} | options
    result = coverage(items, **fields)
    return {m["name"]: m["value"] for m in result.measurements()}


def reasons(items: list[Record], key: str, **options: Any) -> list[str]:
    fields: dict[str, Any] = {"subject": BUILD, "intents": [INTENT]} | options
    result = coverage(items, **fields)
    criterion = next(c for c in result.criteria if c.reference.endswith(f"#{key}"))
    return [why for _, why in criterion.reasons]


SCOPE = [f"{INTENT}#AC-1", f"{INTENT}#AC-2"]


# --- the worked example of the specification (Appendix A.2) ------------------------------------


def test_the_worked_example_has_coverage_one_half() -> None:
    result = passport_coverage(passport())
    assert [c.reference for c in result.criteria] == SCOPE
    assert [c.counted for c in result.criteria] == [(), (EVIDENCE,)]
    assert result.measurements()[0] == {"name": "intentCoverage", "value": 0.5, "method": METHOD}


def test_the_variants_of_the_worked_example() -> None:
    assert values(records(), scope=SCOPE) == {
        "intentCoverage": 0.5,
        # the passport is unsigned, so every context is self-declared (R12)
        "intentCoverageAttested": 0.0,
        "intentCoverageWeighted": 0.5,  # AC-1 and AC-2 are both high
        # the QE agent runs on another provider's model than the developer agent
        "intentCoverageDiverse": 0.5,
    }


def test_without_scope_every_criterion_of_the_intent_counts() -> None:
    measured = values(records())
    assert measured["intentCoverage"] == pytest.approx(1 / 3, abs=1e-4)
    assert measured["intentCoverage"] == 0.3333
    # AC-1 high 3, AC-2 high 3, NFR-1 medium 2: 3 of 8
    assert measured["intentCoverageWeighted"] == 0.375


def test_a_narrower_scope() -> None:
    assert values(records(), scope=[f"{INTENT}#AC-2"])["intentCoverage"] == 1.0


def test_no_criteria_in_scope_gives_no_measurement() -> None:
    other = f"{NS}/intents/INT-999@1#AC-1"
    assert coverage(records(), subject=BUILD, intents=[INTENT], scope=[other]).measurements() == []


# --- counted evidence ---------------------------------------------------------------------------


def changed_evidence(**fields: Any) -> list[Record]:
    items = copy.deepcopy(records())
    by_id(items, EVIDENCE).update(fields)
    return items


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"result": "fail"}, "result is fail"),
        ({"derivedFrom": "implementation"}, "derivedFrom is implementation"),
        ({"subject": f"{NS}/artifacts/ART-301"}, "neither the release subject nor"),
        ({"observedAt": "2026-10-18T16:40:00Z"}, "not after its subject was created"),
    ],
)
def test_evidence_that_does_not_count(fields: Record, reason: str) -> None:
    items = changed_evidence(**fields)
    assert values(items, scope=SCOPE)["intentCoverage"] == 0.0
    assert any(reason in why for why in reasons(items, "AC-2", scope=SCOPE))


def test_evidence_without_subject_does_not_count() -> None:
    items = copy.deepcopy(records())
    del by_id(items, EVIDENCE)["subject"]
    assert reasons(items, "AC-2") == ["no subject"]


def test_evidence_for_the_build_itself_counts_once_the_build_exists() -> None:
    before = changed_evidence(subject=BUILD)  # observed 14:31, the build was created at 16:10
    assert values(before, scope=SCOPE)["intentCoverage"] == 0.0
    after = changed_evidence(subject=BUILD, observedAt="2026-10-20T16:30:00Z")
    assert values(after, scope=SCOPE)["intentCoverage"] == 0.5


def test_revoked_evidence_does_not_count() -> None:
    items = copy.deepcopy(records())
    items.append(
        {
            "ois": "0.1",
            "type": "decision",
            "id": f"{NS}/decisions/DEC-90",
            "createdAt": "2026-10-21T08:00:00Z",
            "gate": "evidence-revocation",
            "subject": EVIDENCE,
            "outcome": "rejected",
            "decidedBy": f"{NS}/actors/qe-2c9d",
            "decidedAt": "2026-10-21T08:00:00Z",
            "rationale": "flaky",
        }
    )
    assert values(items, scope=SCOPE)["intentCoverage"] == 0.0
    assert reasons(items, "AC-2") == [f"revoked by {NS}/decisions/DEC-90"]


def test_a_criterion_without_evidence_has_no_reasons() -> None:
    assert reasons(records(), "AC-1") == []


# --- targets -------------------------------------------------------------------------------------


def nfr_evidence(measures: list[Record]) -> list[Record]:
    items = copy.deepcopy(records())
    nfr = copy.deepcopy(by_id(items, EVIDENCE))
    nfr |= {"id": f"{NS}/evidence/EV-9030", "criterion": f"{INTENT}#NFR-1", "measures": measures}
    items.append(nfr)
    return items


@pytest.mark.parametrize(
    ("measures", "covered"),
    [
        ([{"name": "responseTimeP95", "value": 420, "unit": "ms"}], True),
        ([{"name": "responseTimeP95", "value": 500, "unit": "ms"}], True),  # lte
        ([{"name": "responseTimeP95", "value": 501, "unit": "ms"}], False),
        ([{"name": "responseTimeP50", "value": 100, "unit": "ms"}], False),
        ([{"name": "responseTimeP95", "value": "fast"}], False),
        ([], False),
    ],
)
def test_a_target_must_be_met(measures: list[Record], covered: bool) -> None:
    result = coverage(nfr_evidence(measures), subject=BUILD, intents=[INTENT])
    nfr = next(c for c in result.criteria if c.reference.endswith("#NFR-1"))
    assert nfr.covered is covered
    if not covered:
        assert "responseTimeP95 lte 500" in nfr.reasons[0][1]


# --- stale evidence (section 6.3) ---------------------------------------------------------------


def second_version(change_ac2: bool) -> list[Record]:
    items = copy.deepcopy(records())
    first = next(r for r in items if r["type"] == "intent")
    draft = next_version(first, created_at=datetime(2026, 10, 19, tzinfo=UTC))
    if change_ac2:
        criterion = next(c for c in draft["criteria"] if c["key"] == "AC-2")
        criterion["statement"] += " immediately"
    approved, approval = approve(
        draft, decided_by=f"{NS}/actors/po-7f3a", decided_at=datetime(2026, 10, 19, 1, tzinfo=UTC)
    )
    return [*items, approved, approval]


def test_evidence_carries_over_to_an_unchanged_criterion() -> None:
    items = second_version(change_ac2=False)
    v2 = f"{NS}/intents/INT-142@2"
    measured = values(items, intents=[v2], scope=[f"{v2}#AC-1", f"{v2}#AC-2"])
    assert measured["intentCoverage"] == 0.5


def test_evidence_for_a_changed_criterion_is_stale() -> None:
    items = second_version(change_ac2=True)
    v2 = f"{NS}/intents/INT-142@2"
    assert values(items, intents=[v2])["intentCoverage"] == 0.0
    assert reasons(items, "AC-2", intents=[v2]) == ["stale: the criterion changed since version 1"]


# --- diverse producers ----------------------------------------------------------------------------


def test_agents_on_the_same_model_are_not_diverse() -> None:
    items = copy.deepcopy(records())
    qe = by_id(items, f"{NS}/actors/qe-agent")
    qe["model"] = {"provider": "ExampleAI", "name": "model-a", "version": "2026-10"}
    assert values(items, scope=SCOPE)["intentCoverageDiverse"] == 0.0


def test_a_human_producer_is_diverse() -> None:
    items = copy.deepcopy(records())
    by_id(items, f"{NS}/artifacts/ART-304")["producedBy"] = f"{NS}/actors/qe-2c9d"
    by_id(items, f"{NS}/actors/qe-agent")["model"] = by_id(items, f"{NS}/actors/dev-agent")["model"]
    assert values(items, scope=SCOPE)["intentCoverageDiverse"] == 0.5


def test_several_intents_are_counted_together() -> None:
    items = copy.deepcopy(records())
    other = copy.deepcopy(next(r for r in items if r["type"] == "intent"))
    other["id"] = f"{NS}/intents/INT-143"
    other["criteria"] = other["criteria"][:1]
    for criterion in other["criteria"]:
        criterion["digest"] = {"sha256": digests.criterion_sha256(criterion)}
    other["digest"] = {"sha256": digests.intent_sha256(other)}
    items.append(other)
    result = coverage(items, subject=BUILD, intents=[INTENT, f"{NS}/intents/INT-143@1"])
    assert len(result.criteria) == 4
    assert result.measurements()[0]["value"] == 0.25


def test_the_release_subject_is_needed() -> None:
    statement = passport()
    statement["predicate"]["records"] = [
        r for r in statement["predicate"]["records"] if r["id"] != f"{NS}/decisions/DEC-81"
    ]
    with pytest.raises(CoverageError, match="release"):
        passport_coverage(statement)


# --- in a passport and on the command line -------------------------------------------------------


def test_ois_passport_records_the_coverage() -> None:
    made = build(
        collect(records()),
        release=Release("customer-portal", "2026.10.3"),
        build=BUILD,
        issued_by=f"{NS}/actors/passport-issuer@1.0",
        issued_at=datetime(2026, 10, 21, 9, tzinfo=UTC),
        scope=SCOPE,
    )
    assessments = made["predicate"]["assessments"]
    assert [a["name"] for a in assessments] == [
        "intentCoverage",
        "intentCoverageAttested",
        "intentCoverageWeighted",
        "intentCoverageDiverse",
    ]
    assert all(a["method"] == METHOD for a in assessments)
    assert (
        list(made["predicate"]).index("assessments")
        == list(made["predicate"]).index("issuedBy") - 1
    )


def test_ois_passport_without_coverage(tmp_path: Path) -> None:
    source = tmp_path / "passport-in.json"
    source.write_text(json.dumps(passport()), encoding="utf-8")
    out = tmp_path / "passport.json"
    args = ["build", str(source), "--release-name", "p", "--release-version", "1", "--build", BUILD]
    args += ["--issuer", f"{NS}/actors/passport-issuer@1.0", "--no-coverage", "--output", str(out)]
    assert passport_main(args) == 0
    assert "assessments" not in json.loads(out.read_text())["predicate"]


def test_cli_checks_the_declared_coverage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = tmp_path / "passport.json"
    good.write_text(json.dumps(passport()), encoding="utf-8")
    assert main([str(good)]) == 0
    out = capsys.readouterr().out
    assert f"{INTENT}#AC-1  not covered" in out
    assert f"{INTENT}#AC-2  covered  {EVIDENCE}" in out
    assert "intentCoverage 0.5 (declared 0.5)" in out

    claimed = passport()
    claimed["predicate"]["assessments"][0]["value"] = 0.9
    bad = tmp_path / "claimed.json"
    bad.write_text(json.dumps(claimed), encoding="utf-8")
    assert main([str(bad)]) == 1
    assert "declared intentCoverage 0.9, computed 0.5" in capsys.readouterr().err


def test_cli_rounds_to_the_declared_precision(tmp_path: Path) -> None:
    statement = passport()
    statement["predicate"]["scope"] = None
    del statement["predicate"]["scope"]
    statement["predicate"]["assessments"][0]["value"] = 0.33
    path = tmp_path / "passport.json"
    path.write_text(json.dumps(statement), encoding="utf-8")
    assert main([str(path)]) == 0


def test_cli_on_records_before_the_passport(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / ".ois"
    directory.mkdir()
    for index, record in enumerate(records()):
        (directory / f"{index}.ois.json").write_text(json.dumps(record), encoding="utf-8")
    assert main([str(directory), "--build", BUILD, "--scope", f"{INTENT}#AC-2"]) == 0
    assert "intentCoverage 1.0" in capsys.readouterr().out
    assert main([str(directory)]) == 2  # records need --build


def test_cli_writes_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    claimed = passport()
    claimed["predicate"]["assessments"][0]["value"] = 0.9
    path = tmp_path / "passport.json"
    path.write_text(json.dumps(claimed), encoding="utf-8")
    assert main([str(path), "--format", "markdown"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("### Intent coverage\n")
    assert "| intentCoverage | 50 % | 90 %, differs |" in out
    assert "| `INT-142@1#AC-2` | WHEN an address cannot be assigned to a canton, THE portal" in out
    assert "… | covered | `EV-9021` |" in out
    assert "| `INT-142@1#AC-1` | Given a logged-in customer" in out
    assert "| not covered | `EV-" in out or "| not covered | no evidence |" in out


def test_markdown_escapes_table_cells() -> None:
    from ois_tools.coverage_cli import cell

    assert cell("a | b\nc") == "a \\| b c"
