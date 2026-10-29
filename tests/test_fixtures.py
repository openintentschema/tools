"""The conformance fixtures (section 11.3): exactly the (rule, level) pairs of the manifest, for the
rules in IMPLEMENTED."""

import json
from pathlib import Path
from typing import Any

import pytest

from ois_tools import validate
from ois_tools.keyless import load_trusted_roots
from ois_tools.signing import load_public_key

FIXTURES = Path(__file__).parent / "fixtures"
IMPLEMENTED = {
    "schema",
    "signature",
    *(f"R{n}" for n in range(1, 18) if n not in (6, 7)),
    *(f"regulated/P{n}" for n in range(1, 5)),
}

_MANIFEST: dict[str, Any] = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
MANIFEST: list[dict[str, Any]] = _MANIFEST["fixtures"]
TRUSTED = [load_public_key((FIXTURES / path).read_bytes()) for path in _MANIFEST["trustedKeys"]]
ROOTS = load_trusted_roots(str(FIXTURES / path) for path in _MANIFEST["trustedRoots"])


@pytest.mark.parametrize("entry", MANIFEST, ids=[entry["path"] for entry in MANIFEST])
def test_fixture(entry: dict[str, Any]) -> None:
    document = json.loads((FIXTURES / entry["path"]).read_text(encoding="utf-8"))
    expected = {(e["rule"], e["level"]) for e in entry["expect"] if e["rule"] in IMPLEMENTED}
    result = validate(document, trusted_keys=TRUSTED, trusted_roots=ROOTS)
    reported = {(r.rule, str(r.level)) for r in result.reports}
    assert reported == expected, entry["description"]


def test_every_fixture_is_in_the_manifest() -> None:
    fixtures = [p for folder in ("valid", "invalid") for p in (FIXTURES / folder).glob("**/*.json")]
    on_disk = {str(p.relative_to(FIXTURES)) for p in fixtures}
    assert on_disk == {entry["path"] for entry in MANIFEST}
