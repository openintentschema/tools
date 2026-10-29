import json
from pathlib import Path
from typing import Any

import pytest

from ois_tools import validate
from ois_tools.cli import main as ois
from ois_tools.profiles import REGULATED, ProfileError, resolve
from ois_tools.signing import generate_key

FIXTURES = Path(__file__).parent / "fixtures"
NS = "https://ois.acme.example"


def fixture(path: str) -> Any:
    return json.loads((FIXTURES / path).read_text(encoding="utf-8"))


def rules(document: Any, **options: Any) -> set[tuple[str, str]]:
    return {(r.rule, str(r.level)) for r in validate(document, **options).reports}


def test_profiles_by_name_or_uri() -> None:
    assert resolve("regulated/v0.1") == REGULATED
    assert resolve(REGULATED) == REGULATED
    with pytest.raises(ProfileError, match="unknown profile"):
        resolve("banking/v1")


def test_a_profile_no_record_lists_is_not_applied() -> None:
    assert rules(fixture("valid/passport.json")) == {("R12", "warning")}


def test_a_profile_can_be_applied_by_the_verifier() -> None:
    forced = rules(fixture("valid/passport.json"), profiles=[REGULATED])
    assert forced == {("regulated/P3", "violation"), ("R12", "warning")}


def test_cli_validate_with_a_profile(capsys: pytest.CaptureFixture[str]) -> None:
    path = str(FIXTURES / "valid" / "passport.json")
    assert ois(["validate", path, "--profile", "regulated/v0.1"]) == 1
    assert "[regulated/P3]" in capsys.readouterr().out
    assert ois(["validate", path, "--profile", "banking/v1"]) == 2


def test_a_regulated_passport_is_built_unsigned_and_signed_afterwards(tmp_path: Path) -> None:
    """P3 holds only once the passport is signed, so building and signing do not refuse it."""
    statement = fixture("invalid/regulated/P3/unsigned-passport.json")
    source = tmp_path / "records.json"
    source.write_text(json.dumps(statement), encoding="utf-8")
    out = tmp_path / "passport.json"
    args = ["passport", "build", str(source), "--release-name", "p", "--release-version", "1"]
    args += ["--build", f"{NS}/artifacts/ART-305", "--issuer", f"{NS}/actors/passport-issuer@1.0"]
    assert ois([*args, "--output", str(out)]) == 0

    private_pem, _, _ = generate_key()
    key = tmp_path / "issuer.key"
    key.write_bytes(private_pem)
    signed = tmp_path / "passport.dsse.json"
    assert ois(["sign", str(out), "--key", str(key), "--output", str(signed)]) == 0
    assert ("regulated/P3", "violation") not in rules(json.loads(signed.read_text()))
