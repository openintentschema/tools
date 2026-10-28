"""Sigstore bundles (section 8.3): verified offline against chosen trusted roots; the signer is the
certificate identity (OIDC issuer, subject). Needs the `sigstore` extra."""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.x509.oid import ObjectIdentifier

from ois_tools.document import Signers
from ois_tools.report import Level, Report
from ois_tools.signing import Opened, SigningError, read_payload

MEDIA_TYPE = "application/vnd.dev.sigstore.bundle"
OIDC_ISSUER = ObjectIdentifier("1.3.6.1.4.1.57264.1.8")  # DER UTF8String
OIDC_ISSUER_V1 = ObjectIdentifier("1.3.6.1.4.1.57264.1.1")  # raw bytes
INSTALL = "install ois-tools[sigstore] to verify Sigstore bundles"


def is_sigstore_bundle(document: object) -> bool:
    return isinstance(document, dict) and str(document.get("mediaType", "")).startswith(MEDIA_TYPE)


def load_trusted_roots(specs: Iterable[str]) -> list[Any]:
    """Trusted roots from files: one trusted root (JSON), or one per line as
    `gh attestation trusted-root` writes them (JSON lines)."""
    specs = list(specs)
    if not specs:
        return []
    models = _sigstore_models()
    roots = []
    for spec in specs:
        text = Path(spec).read_text(encoding="utf-8")
        try:
            json.loads(text)
            documents = [text]
        except json.JSONDecodeError:
            documents = [line for line in text.splitlines() if line.strip()]
        for document in documents:
            try:
                inner = models.trustroot_v1.TrustedRoot.from_json(document)
                roots.append(models.TrustedRoot(inner))
            except Exception as error:  # the library raises its own validation errors
                raise SigningError(f"{spec}: not a Sigstore trusted root: {error}") from error
    return roots


def open_sigstore_bundle(bundle: dict[str, Any], roots: Iterable[Any]) -> Opened:
    envelope = bundle.get("dsseEnvelope")
    if not isinstance(envelope, dict):
        message = "the bundle carries no DSSE envelope, so it cannot hold an OIS document"
        return Opened(None, reports=[_violation(message, "$")])
    payload, _, reports = read_payload(envelope, "$.dsseEnvelope")
    if payload is None:
        return Opened(None, reports=reports)
    roots = list(roots)
    if not roots:
        message = "no trusted root given (--trusted-root), so the signature cannot be checked"
        reports.append(Report(Level.WARNING, "signature", message, "$.verificationMaterial"))
        return Opened(payload, Signers(), reports)
    try:
        identity = _verify(bundle, roots)
    except SigningError as error:
        reports.append(_violation(str(error), "$.verificationMaterial"))
        return Opened(payload, Signers(), reports)
    unsigned = any(r.level == Level.VIOLATION for r in reports)
    signers = Signers() if unsigned else Signers(identities=frozenset({identity}))
    return Opened(payload, signers, reports)


class _Recorder:
    """A verification policy that accepts any identity and keeps the one it saw: the identity
    is matched to actors afterwards, by R12, not fixed in advance."""

    identity: tuple[str, str] | None = None

    def verify(self, cert: x509.Certificate) -> None:
        self.identity = (_issuer(cert), _subject(cert))


def _verify(bundle: dict[str, Any], roots: list[Any]) -> tuple[str, str]:
    """Certificate chain, transparency log entry and signature against any of the roots."""
    models = _sigstore_models()
    from sigstore.verify import Verifier  # optional dependency

    try:
        parsed = models.Bundle.from_json(json.dumps(bundle))
    except Exception as error:
        raise SigningError(f"not a valid Sigstore bundle: {error}") from error
    failures = []
    for root in roots:
        recorder = _Recorder()
        try:
            Verifier(trusted_root=root).verify_dsse(parsed, recorder)
        except Exception as error:  # VerificationError and errors from malformed material
            failures.append(str(error))
            continue
        if recorder.identity is not None:
            return recorder.identity
    raise SigningError(f"the signature does not verify against any trusted root: {failures[-1]}")


def _issuer(cert: x509.Certificate) -> str:
    for oid, der in ((OIDC_ISSUER, True), (OIDC_ISSUER_V1, False)):
        try:
            value = cert.extensions.get_extension_for_oid(oid).value
        except x509.ExtensionNotFound:
            continue
        raw = value.value if isinstance(value, x509.UnrecognizedExtension) else b""
        return _der_utf8(raw) if der else raw.decode()
    return ""


def _subject(cert: x509.Certificate) -> str:
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    for kind in (x509.UniformResourceIdentifier, x509.RFC822Name, x509.OtherName):
        found = names.get_values_for_type(kind)
        if found and isinstance(found[0], str):
            return found[0]
    return ""


def _der_utf8(raw: bytes) -> str:
    """The text of a DER UTF8String (tag 0x0c, short or long length form)."""
    length, start = raw[1], 2
    if length & 0x80:
        count = length & 0x7F
        length, start = int.from_bytes(raw[2 : 2 + count], "big"), 2 + count
    return raw[start : start + length].decode()


def _sigstore_models() -> Any:
    try:
        import sigstore.models as models  # optional dependency
    except ImportError as error:
        raise SigningError(INSTALL) from error
    return models


def _violation(message: str, path: str) -> Report:
    return Report(Level.VIOLATION, "signature", message, path)
