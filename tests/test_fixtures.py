"""The conformance fixtures of the specification (section 11.3).

Each fixture lists the exact (rule, level) pairs a validator must report. Only the rules this
validator implements are compared, so a fixture for R5 already proves that its document passes
the schema check. Add a rule to IMPLEMENTED once it is checked.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from ois_tools import validate
from ois_tools.signing import load_public_key

FIXTURES = Path(__file__).parent / "fixtures"
IMPLEMENTED = {"schema", "signature", *(f"R{n}" for n in range(1, 18) if n not in (6, 7))}

_MANIFEST: dict[str, Any] = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
MANIFEST: list[dict[str, Any]] = _MANIFEST["fixtures"]
TRUSTED = [load_public_key((FIXTURES / path).read_bytes()) for path in _MANIFEST["trustedKeys"]]


@pytest.mark.parametrize("entry", MANIFEST, ids=[entry["path"] for entry in MANIFEST])
def test_fixture(entry: dict[str, Any]) -> None:
    document = json.loads((FIXTURES / entry["path"]).read_text(encoding="utf-8"))
    expected = {(e["rule"], e["level"]) for e in entry["expect"] if e["rule"] in IMPLEMENTED}
    reported = {(r.rule, str(r.level)) for r in validate(document, trusted_keys=TRUSTED).reports}
    assert reported == expected, entry["description"]


def test_every_fixture_is_in_the_manifest() -> None:
    on_disk = {str(p.relative_to(FIXTURES)) for p in FIXTURES.glob("*/**/*.json")}
    assert on_disk == {entry["path"] for entry in MANIFEST}
