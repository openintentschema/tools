"""Sigstore bundles (keyless signing). The test bundle is the real provenance attestation of the
ois-tools 0.6.0 image, signed by GitHub Actions through the public-good Sigstore instance; the
trusted root was fetched once from Sigstore's TUF repository. Both are frozen, so the tests run
without network."""

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from ois_tools import validate
from ois_tools.cli import main as ois
from ois_tools.document import Document, Signers
from ois_tools.keyless import is_sigstore_bundle, load_trusted_roots, open_sigstore_bundle
from ois_tools.rules import self_declared

HERE = Path(__file__).parent
BUNDLE = HERE / "sigstore" / "ois-tools-0.6.0-image.sigstore.json"
ROOT = HERE / "sigstore" / "public-good.trusted_root.json"
GITHUB = "https://token.actions.githubusercontent.com"
WORKFLOW = (
    "https://github.com/openintentschema/tools/.github/workflows/release.yml@refs/tags/v0.6.0"
)
NS = "https://ois.acme.example"


def bundle() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(BUNDLE.read_text(encoding="utf-8"))
    return loaded


def rules(reports: list[Any]) -> set[tuple[str, str]]:
    return {(r.rule, str(r.level)) for r in reports}


def test_a_sigstore_bundle_is_recognised() -> None:
    assert is_sigstore_bundle(bundle())
    assert not is_sigstore_bundle({"payloadType": "x", "payload": "", "signatures": []})


def test_verified_with_a_trusted_root_names_the_workflow() -> None:
    opened = open_sigstore_bundle(bundle(), load_trusted_roots([str(ROOT)]))
    assert opened.signers.identities == {(GITHUB, WORKFLOW)}
    assert opened.payload["predicateType"] == "https://slsa.dev/provenance/v1"
    assert opened.reports == []  # an in-toto Statement, signed and verified


def test_without_a_trusted_root_nothing_is_verified() -> None:
    opened = open_sigstore_bundle(bundle(), [])
    assert not opened.signers
    assert ("signature", "warning") in rules(opened.reports)


def test_a_changed_payload_does_not_verify() -> None:
    changed = bundle()
    statement = json.loads(base64.b64decode(changed["dsseEnvelope"]["payload"]))
    statement["predicate"]["buildDefinition"]["externalParameters"]["workflow"]["ref"] = "x"
    changed["dsseEnvelope"]["payload"] = base64.b64encode(json.dumps(statement).encode()).decode()
    opened = open_sigstore_bundle(changed, load_trusted_roots([str(ROOT)]))
    assert not opened.signers
    assert any("does not verify" in r.message for r in opened.reports)


def test_a_root_without_the_certificate_authority_does_not_verify(tmp_path: Path) -> None:
    root = json.loads(ROOT.read_text(encoding="utf-8"))
    root["certificateAuthorities"] = []
    other = tmp_path / "other.trusted_root.json"
    other.write_text(json.dumps(root), encoding="utf-8")
    opened = open_sigstore_bundle(bundle(), load_trusted_roots([str(other)]))
    assert not opened.signers
    assert ("signature", "violation") in rules(opened.reports)


def test_trusted_roots_as_json_lines(tmp_path: Path) -> None:
    """gh attestation trusted-root writes one trusted root per line."""
    one_line = json.dumps(json.loads(ROOT.read_text(encoding="utf-8")))
    lines = tmp_path / "trusted_root.jsonl"
    lines.write_text(f"{one_line}\n{one_line}\n", encoding="utf-8")
    assert len(load_trusted_roots([str(lines)])) == 2


def test_a_certificate_identity_attests_a_context() -> None:
    runtime = {
        "ois": "0.1", "type": "actor", "id": f"{NS}/actors/agent-runtime",
        "createdAt": "2026-09-01T08:00:00Z", "kind": "tool", "name": "Agent runtime",
        "version": "4.3", "identity": {"issuer": GITHUB, "subject": WORKFLOW},
    }  # fmt: skip
    test = {
        "ois": "0.1", "type": "artifact", "id": f"{NS}/artifacts/T-1",
        "createdAt": "2026-10-18T17:05:00Z", "kind": "test", "location": "git+https://x@1",
        "digest": {"gitCommit": "a1"}, "producedBy": f"{NS}/actors/qe-agent@2.1",
        "context": [f"{NS}/intents/INT-1@1"], "contextRecordedBy": f"{NS}/actors/agent-runtime@4.3",
    }  # fmt: skip
    records = {
        "ois": "0.1", "type": "bundle", "id": f"{NS}/bundles/B",
        "createdAt": "2026-10-20T00:00:00Z", "records": [runtime, test],
    }  # fmt: skip
    signed = Document.of(records, signers=Signers(identities=frozenset({(GITHUB, WORKFLOW)})))
    assert self_declared(test, signed) is None
    other = Signers(identities=frozenset({(GITHUB, WORKFLOW.replace("v0.6.0", "v0.7.0"))}))
    assert self_declared(test, Document.of(records, signers=other)) is not None


def test_validate_reads_a_sigstore_bundle() -> None:
    result = validate(bundle(), trusted_roots=load_trusted_roots([str(ROOT)]))
    # the signature holds; the payload is SLSA provenance, not an OIS passport
    assert rules(result.reports) == {("schema", "violation")}


def test_cli_validate_with_a_trusted_root(capsys: pytest.CaptureFixture[str]) -> None:
    assert ois(["validate", str(BUNDLE), "--trusted-root", str(ROOT)]) == 1
    out = capsys.readouterr().out
    assert "predicateType" in out
    assert "[signature]" not in out


def test_passport_build_counts_a_keyless_signer(tmp_path: Path) -> None:
    """Signed afterwards with actions/attest, the passport's attested coverage must already count
    the workflow that will sign it."""
    statement = json.loads((HERE / "fixtures" / "valid" / "passport.json").read_text())
    runtime = next(
        r for r in statement["predicate"]["records"] if r["id"].endswith("agent-runtime")
    )
    runtime["identity"] = {"issuer": GITHUB, "subject": WORKFLOW}
    source = tmp_path / "records.json"
    source.write_text(json.dumps(statement), encoding="utf-8")
    out = tmp_path / "passport.json"
    args = ["passport", "build", str(source), "--release-name", "p", "--release-version", "1"]
    args += ["--build", f"{NS}/artifacts/ART-305", "--issuer", f"{NS}/actors/passport-issuer@1.0"]
    args += ["--scope", f"{NS}/intents/INT-142@1#AC-1", "--scope", f"{NS}/intents/INT-142@1#AC-2"]
    args += ["--keyless-signer", f"{GITHUB}={WORKFLOW}", "--output", str(out)]
    assert ois(args) == 0
    assessments = json.loads(out.read_text())["predicate"]["assessments"]
    attested = next(a for a in assessments if a["name"] == "intentCoverageAttested")
    assert attested["value"] == 0.5
    assert ois([*args[:-2], "--keyless-signer", "no-equals-sign", "--output", str(out)]) == 1
