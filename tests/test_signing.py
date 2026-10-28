import base64
import json
import stat
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ois_tools import validate
from ois_tools.cli import main as ois
from ois_tools.coverage import passport_coverage
from ois_tools.signing import (
    IN_TOTO,
    OIS_BUNDLE,
    SigningError,
    generate_key,
    load_private_key,
    load_public_key,
    open_envelope,
    sign,
)

FIXTURES = Path(__file__).parent / "fixtures"
NS = "https://ois.acme.example"
RUNTIME = f"{NS}/actors/agent-runtime"


def fixture(path: str) -> Any:
    return json.loads((FIXTURES / path).read_text(encoding="utf-8"))


def trusted(*names: str) -> list[Any]:
    return [load_public_key((FIXTURES / "keys" / f"{name}.pub").read_bytes()) for name in names]


def bundle_with_runtime_key(public_pem: bytes) -> dict[str, Any]:
    """The fixture bundle, with the agent runtime holding the given key."""
    document: dict[str, Any] = fixture("valid/bundle.json")
    runtime = next(r for r in document["records"] if r["id"] == RUNTIME)
    runtime["identity"] = {**runtime["identity"], "keyId": load_public_key(public_pem).keyid}
    return document


def rules(result: Any) -> set[tuple[str, str]]:
    return {(r.rule, str(r.level)) for r in result.reports}


# --- keys --------------------------------------------------------------------------------------


def test_key_ids_match_the_fixtures() -> None:
    """Key ids are derived as the specification's fixtures derive them, independently."""
    envelope = fixture("valid/signed-bundle.json")
    ids = {s["keyid"] for s in envelope["signatures"]}
    assert ids == {k.keyid for k in trusted("agent-runtime", "ci-recorder")}


def test_a_generated_key_pair() -> None:
    private_pem, public_pem, keyid = generate_key()
    assert b"PRIVATE KEY" in private_pem
    assert load_public_key(public_pem).keyid == keyid
    assert len(keyid) == 64


def test_a_key_id_can_be_given_explicitly() -> None:
    _, public_pem, _ = generate_key()
    assert load_public_key(public_pem, keyid="kms-key-7").keyid == "kms-key-7"


# --- signing and verifying -----------------------------------------------------------------------


def test_signed_by_the_recorder_attests_the_contexts() -> None:
    private_pem, public_pem, _ = generate_key()
    document = bundle_with_runtime_key(public_pem)
    envelope = sign(document, load_private_key(private_pem))
    assert envelope["payloadType"] == OIS_BUNDLE
    assert json.loads(base64.b64decode(envelope["payload"])) == document
    result = validate(envelope, trusted_keys=[load_public_key(public_pem)])
    assert result.reports == []


def test_without_trusting_the_key_nothing_is_attested() -> None:
    private_pem, public_pem, _ = generate_key()
    envelope = sign(bundle_with_runtime_key(public_pem), load_private_key(private_pem))
    assert rules(validate(envelope)) == {("signature", "warning"), ("R12", "warning")}


def test_a_passport_gets_the_in_toto_payload_type() -> None:
    private_pem, _, _ = generate_key()
    envelope = sign(fixture("valid/passport.json"), load_private_key(private_pem))
    assert envelope["payloadType"] == IN_TOTO


def test_a_second_signature_is_added_to_the_envelope() -> None:
    first_private, first_public, first_id = generate_key()
    second_private, second_public, second_id = generate_key()
    document = bundle_with_runtime_key(second_public)
    once = sign(document, load_private_key(first_private))
    twice = sign(once, load_private_key(second_private))
    assert [s["keyid"] for s in twice["signatures"]] == [first_id, second_id]
    assert twice["payload"] == once["payload"]
    keys = [load_public_key(first_public), load_public_key(second_public)]
    assert validate(twice, trusted_keys=keys).reports == []
    with pytest.raises(SigningError, match="already signed"):
        sign(twice, load_private_key(second_private))


def test_a_changed_payload_does_not_verify() -> None:
    private_pem, public_pem, _ = generate_key()
    envelope = sign(bundle_with_runtime_key(public_pem), load_private_key(private_pem))
    changed = json.loads(base64.b64decode(envelope["payload"]))
    changed["createdAt"] = "2026-10-21T15:00:00Z"
    envelope["payload"] = base64.b64encode(json.dumps(changed).encode()).decode()
    result = validate(envelope, trusted_keys=[load_public_key(public_pem)])
    assert rules(result) == {("signature", "violation"), ("R12", "warning")}
    assert not result.conforms


def test_ecdsa_p256_keys_work_too() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    envelope = sign(bundle_with_runtime_key(public_pem), load_private_key(private_pem))
    assert validate(envelope, trusted_keys=[load_public_key(public_pem)]).reports == []


def test_only_bundles_and_passports_are_signed() -> None:
    private_pem, _, _ = generate_key()
    with pytest.raises(SigningError, match="bundle or a passport"):
        sign(fixture("valid/intent.json"), load_private_key(private_pem))


def test_a_payload_that_is_not_json() -> None:
    envelope: dict[str, Any] = {
        "payloadType": OIS_BUNDLE,
        "payload": base64.b64encode(b"<xml/>").decode(),
        "signatures": [{"keyid": "x", "sig": "AAAA"}],
    }
    opened = open_envelope(envelope, trusted=[])
    assert opened.payload is None
    assert ("signature", "violation") in {(r.rule, str(r.level)) for r in opened.reports}


# --- coverage -------------------------------------------------------------------------------------


def test_attested_coverage_of_the_signed_passport() -> None:
    envelope = fixture("valid/signed-passport.json")
    keys = trusted("agent-runtime", "ci-recorder", "passport-issuer")
    measured = {m["name"]: m["value"] for m in passport_coverage(envelope, keys).measurements()}
    assert measured["intentCoverage"] == 0.5
    assert measured["intentCoverageAttested"] == 0.5
    unverified = {m["name"]: m["value"] for m in passport_coverage(envelope).measurements()}
    assert unverified["intentCoverageAttested"] == 0.0


# --- command line ---------------------------------------------------------------------------------


def test_cli_generate_sign_validate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ois(["key", "generate", "runtime", "--output-dir", str(tmp_path)]) == 0
    keyid = capsys.readouterr().out.strip()
    private, public = tmp_path / "runtime.key", tmp_path / "runtime.pub"
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert ois(["key", "id", str(public)]) == 0
    assert capsys.readouterr().out.strip() == keyid

    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(bundle_with_runtime_key(public.read_bytes())), encoding="utf-8")
    signed = tmp_path / "bundle.dsse.json"
    assert ois(["sign", str(source), "--key", str(private), "--output", str(signed)]) == 0
    assert ois(["validate", str(signed), "--trusted-key", str(public)]) == 0
    assert capsys.readouterr().out.strip().endswith("bundle.dsse.json: conforms")
    assert ois(["validate", str(signed)]) == 0  # unverifiable is a warning
    assert "[signature]" in capsys.readouterr().out


def test_cli_trusts_a_directory_of_keys(capsys: pytest.CaptureFixture[str]) -> None:
    path = str(FIXTURES / "valid" / "signed-passport.json")
    assert ois(["validate", path, "--trusted-key", str(FIXTURES / "keys")]) == 0
    assert capsys.readouterr().out.strip().endswith("signed-passport.json: conforms")


def test_cli_record_actor_with_its_public_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    public = FIXTURES / "keys" / "agent-runtime.pub"
    args = ["record", "actor", "--kind", "tool", "--name", "Agent runtime", "--version", "4.3"]
    args += ["--public-key", str(public), "--namespace", NS, "--output-dir", str(tmp_path)]
    assert ois(args) == 0
    [path] = (tmp_path / "actors").iterdir()
    record = json.loads(path.read_text())
    assert record["identity"] == {"keyId": trusted("agent-runtime")[0].keyid}


def test_cli_coverage_of_a_signed_passport(capsys: pytest.CaptureFixture[str]) -> None:
    path = str(FIXTURES / "valid" / "signed-passport.json")
    assert ois(["coverage", path, "--trusted-key", str(FIXTURES / "keys")]) == 0
    assert "intentCoverageAttested 0.5" in capsys.readouterr().out


def test_cli_passport_build_reads_signed_bundles(tmp_path: Path) -> None:
    out = tmp_path / "passport.json"
    source = FIXTURES / "valid" / "signed-passport.json"
    args = ["passport", "build", str(source), "--release-name", "p", "--release-version", "1"]
    args += ["--build", f"{NS}/artifacts/ART-305", "--issuer", f"{NS}/actors/passport-issuer@1.0"]
    assert ois([*args, "--output", str(out)]) == 0
    assert json.loads(out.read_text())["_type"] == "https://in-toto.io/Statement/v1"


def test_cli_passport_build_signs_and_counts_its_signers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The attested coverage inside a passport must count the signatures it will carry."""
    private_pem, public_pem, _ = generate_key()
    key, public = tmp_path / "runtime.key", tmp_path / "runtime.pub"
    key.write_bytes(private_pem)
    public.write_bytes(public_pem)
    statement = fixture("valid/passport.json")
    records = statement["predicate"]["records"]
    runtime = next(r for r in records if r["id"] == RUNTIME)
    runtime["identity"] = {**runtime["identity"], "keyId": load_public_key(public_pem).keyid}
    source = tmp_path / "records.json"
    source.write_text(json.dumps(statement), encoding="utf-8")

    out = tmp_path / "passport.dsse.json"
    args = ["passport", "build", str(source), "--release-name", "p", "--release-version", "1"]
    args += ["--build", f"{NS}/artifacts/ART-305", "--issuer", f"{NS}/actors/passport-issuer@1.0"]
    args += ["--scope", f"{NS}/intents/INT-142@1#AC-1", "--scope", f"{NS}/intents/INT-142@1#AC-2"]
    assert ois([*args, "--sign", str(key), "--output", str(out)]) == 0
    assert "R12" not in capsys.readouterr().err  # the signature attests every context
    envelope = json.loads(out.read_text())
    assert envelope["payloadType"] == IN_TOTO
    assert ois(["coverage", str(out), "--trusted-key", str(public)]) == 0
    assert "intentCoverageAttested 0.5 (declared 0.5)" in capsys.readouterr().out
