import copy
import json
from pathlib import Path
from typing import Any

from ois_tools import validate

FIXTURES = Path(__file__).parent / "fixtures"
NS = "https://ois.acme.example"


def load(path: str) -> Any:
    return json.loads((FIXTURES / path).read_text(encoding="utf-8"))


def test_criterion_reference_to_an_actor_is_an_unknown_criterion() -> None:
    bundle = load("valid/bundle.json")
    tool = copy.deepcopy(next(r for r in bundle["records"] if r["type"] == "actor"))
    tool.update(id=f"{NS}/actors/linter", kind="tool", name="Linter", version="1")
    bundle["records"].append(tool)
    index = next(i for i, r in enumerate(bundle["records"]) if r["type"] == "evidence")
    bundle["records"][index]["criterion"] = f"{NS}/actors/linter@1#AC-1"
    reports = [(r.rule, r.path) for r in validate(bundle).reports if r.level == "violation"]
    assert reports == [("R2", f"$.records[{index}].criterion")]
