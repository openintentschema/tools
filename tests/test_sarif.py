import json
from pathlib import Path
from typing import Any

import pytest

from ois_tools import validate
from ois_tools.cli import main as ois
from ois_tools.sarif import line_of, to_sarif

FIXTURES = Path(__file__).parent / "fixtures"


def sarif_for(*paths: str) -> dict[str, Any]:
    results = {}
    for path in paths:
        text = (FIXTURES / path).read_text(encoding="utf-8")
        results[f"fixtures/{path}"] = (text, validate(json.loads(text)))
    return to_sarif(results)


def test_line_of_a_report_path() -> None:
    text = (
        '{\n  "records": [\n    {"id": "a"},\n    {\n      "derivedFrom": "intent"\n    }\n  ]\n}\n'
    )
    assert line_of(text, "$") == 1
    assert line_of(text, "$.records[0]") == 3
    assert line_of(text, "$.records[1].derivedFrom") == 5
    assert line_of(text, "$.records[1].missing") == 4  # the nearest existing member
    assert line_of(text, "$.records[9]") == 2


def test_a_violation_points_at_its_line() -> None:
    log = sarif_for("invalid/R5/same-producer.json")
    assert log["version"] == "2.1.0"
    [run] = log["runs"]
    assert run["tool"]["driver"]["name"] == "ois validate"
    result = next(r for r in run["results"] if r["ruleId"] == "R5")
    assert result["level"] == "error"
    location = result["locations"][0]
    path = location["logicalLocations"][0]["fullyQualifiedName"]
    text = (FIXTURES / "invalid/R5/same-producer.json").read_text(encoding="utf-8")
    assert (
        location["physicalLocation"]["artifactLocation"]["uri"]
        == "fixtures/invalid/R5/same-producer.json"
    )
    assert location["physicalLocation"]["region"]["startLine"] == line_of(text, path)
    rule = run["tool"]["driver"]["rules"][result["ruleIndex"]]
    assert rule["id"] == "R5"
    assert rule["helpUri"].startswith("https://openintentschema.io/specification/")


def test_levels_and_rules() -> None:
    log = sarif_for("valid/bundle.json", "invalid/R8/production-dataset.json", "valid/finding.json")
    run = log["runs"][0]
    assert {r["level"] for r in run["results"]} == {"warning"}  # R8, R12
    assert {rule["id"] for rule in run["tool"]["driver"]["rules"]} == {"R8", "R12"}
    assert all(r["ruleIndex"] < len(run["tool"]["driver"]["rules"]) for r in run["results"])


def test_a_payload_report_points_at_the_payload() -> None:
    log = sarif_for("invalid/regulated/P2/agent-approves-release.json")
    result = next(r for r in log["runs"][0]["results"] if r["ruleId"] == "regulated/P2")
    text = (FIXTURES / "invalid/regulated/P2/agent-approves-release.json").read_text("utf-8")
    assert result["locations"][0]["physicalLocation"]["region"]["startLine"] == line_of(
        text, "$.payload"
    )
    rules = log["runs"][0]["tool"]["driver"]["rules"]
    assert rules[result["ruleIndex"]]["helpUri"].endswith("/profiles/regulated/v0.1/")


def test_cli_writes_sarif(capsys: pytest.CaptureFixture[str]) -> None:
    path = str(FIXTURES / "invalid" / "R5" / "same-producer.json")
    assert ois(["validate", path, "--format", "sarif"]) == 1
    log = json.loads(capsys.readouterr().out)
    assert log["$schema"].endswith("sarif-schema-2.1.0.json")
    assert any(r["ruleId"] == "R5" for r in log["runs"][0]["results"])
    assert ois(["validate", str(FIXTURES / "valid" / "intent.json"), "--format", "sarif"]) == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["results"] == []
