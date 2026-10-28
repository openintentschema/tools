"""DSSE envelopes for bundles and passports (specification section 8.3).

A passport is signed with payload type `application/vnd.in-toto+json`, a bundle with
`application/vnd.ois+json`. Keys are Ed25519 or ECDSA P-256. A key id is the lowercase hex SHA-256
of the key's DER SubjectPublicKeyInfo, so a verifier derives it from the key it trusts. Which keys
are trusted is the verifier's decision; nothing in a document can add one.

`open_envelope` reports under the rule ID `signature` (fixtures/README.md in the specification
repository): a violation for an envelope without signatures, with the wrong payload type or with a
signature under a trusted key that does not verify, a warning for a key that is not trusted. An
envelope with a violation counts as unsigned.
"""

import base64
import binascii
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from ois_tools.document import NOBODY, Signers
from ois_tools.report import Level, Report
from ois_tools.schema import STATEMENT_TYPE

IN_TOTO = "application/vnd.in-toto+json"
OIS_BUNDLE = "application/vnd.ois+json"

PrivateKey = Ed25519PrivateKey | ec.EllipticCurvePrivateKey
PublicKey = Ed25519PublicKey | ec.EllipticCurvePublicKey


class SigningError(ValueError):
    """The document or key cannot be used for signing."""


@dataclass(frozen=True)
class TrustedKey:
    keyid: str
    key: PublicKey


@dataclass
class Opened:
    """What an envelope holds: its payload if it is JSON, who signed it as verified (nobody if it
    counts as unsigned), and the signature reports."""

    payload: Any
    signers: Signers = NOBODY
    reports: list[Report] = field(default_factory=list)


def is_envelope(document: object) -> bool:
    return (
        isinstance(document, dict) and {"payloadType", "payload", "signatures"} <= document.keys()
    )


def key_id(key: PublicKey) -> str:
    der = key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()


def generate_key() -> tuple[bytes, bytes, str]:
    """A new Ed25519 key pair as PEM (private key unencrypted, PKCS #8) and its key id."""
    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key()
    public_pem = public.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem, key_id(public)


def load_public_key(pem: bytes, keyid: str | None = None) -> TrustedKey:
    try:
        key = serialization.load_pem_public_key(pem)
    except ValueError as error:
        raise SigningError(f"not a PEM public key: {error}") from error
    if not isinstance(key, Ed25519PublicKey | ec.EllipticCurvePublicKey):
        raise SigningError("only Ed25519 and ECDSA keys are supported")
    return TrustedKey(keyid or key_id(key), key)


def load_trusted(specs: Iterable[str]) -> list[TrustedKey]:
    """Trusted keys from command line values: a PEM file, a directory of `*.pub` files, or
    `KEYID=FILE` for a key whose id is not derived from it."""
    keys: list[TrustedKey] = []
    for spec in specs:
        keyid, _, name = spec.rpartition("=") if "=" in spec else ("", "", spec)
        path = Path(name)
        files = sorted(path.glob("*.pub")) if path.is_dir() else [path]
        if path.is_dir() and not files:
            raise SigningError(f"{path} holds no *.pub file")
        keys.extend(load_public_key(f.read_bytes(), keyid or None) for f in files)
    return keys


def load_private_key(pem: bytes, password: bytes | None = None) -> PrivateKey:
    try:
        key = serialization.load_pem_private_key(pem, password=password)
    except (ValueError, TypeError) as error:
        raise SigningError(f"cannot read the private key: {error}") from error
    if not isinstance(key, Ed25519PrivateKey | ec.EllipticCurvePrivateKey):
        raise SigningError("only Ed25519 and ECDSA keys are supported")
    return key


def pae(payload_type: str, body: bytes) -> bytes:
    """DSSE v1 pre-authentication encoding of payload type and payload."""
    kind = payload_type.encode()
    return b"DSSEv1 %d %s %d %s" % (len(kind), kind, len(body), body)


def payload_type_of(document: object) -> str:
    if isinstance(document, dict) and document.get("_type") == STATEMENT_TYPE:
        return IN_TOTO
    if isinstance(document, dict) and document.get("type") == "bundle":
        return OIS_BUNDLE
    raise SigningError("only a bundle or a passport is signed (section 8.3)")


def sign(document: dict[str, Any], key: PrivateKey) -> dict[str, Any]:
    """The document in a DSSE envelope signed by `key`; an envelope gets one more signature."""
    if is_envelope(document):
        envelope = json.loads(json.dumps(document))
        payload_type = envelope["payloadType"]
        body = base64.b64decode(envelope["payload"])
    else:
        payload_type = payload_type_of(document)
        body = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
        envelope = {"payloadType": payload_type, "payload": base64.b64encode(body).decode()}
        envelope["signatures"] = []
    keyid = key_id(key.public_key())
    if any(s.get("keyid") == keyid for s in envelope["signatures"]):
        raise SigningError(f"already signed with key {keyid}")
    signature = _sign(key, pae(payload_type, body))
    envelope["signatures"].append({"keyid": keyid, "sig": base64.b64encode(signature).decode()})
    return dict(envelope)


def read_payload(envelope: dict[str, Any], path: str = "$") -> tuple[Any, bytes, list[Report]]:
    """The payload of a DSSE envelope, its bytes, and violations of section 8.3: not base64 JSON
    (payload None), not a bundle or passport, or the wrong payload type."""
    try:
        body = base64.b64decode(envelope.get("payload", ""), validate=True)
        payload = json.loads(body)
    except (binascii.Error, ValueError, TypeError) as error:
        return None, b"", [_violation(f"payload is not base64 JSON: {error}", f"{path}.payload")]
    reports: list[Report] = []
    try:
        required = payload_type_of(payload)
    except SigningError as error:
        reports.append(_violation(str(error), f"{path}.payload"))
    else:
        kind = envelope.get("payloadType")
        if kind != required:
            message = f"payload type {kind!r}; this payload requires {required!r}"
            reports.append(_violation(message, f"{path}.payloadType"))
    return payload, body, reports


def open_envelope(envelope: dict[str, Any], trusted: Iterable[TrustedKey]) -> Opened:
    keys = {k.keyid: k.key for k in trusted}
    payload, body, reports = read_payload(envelope)
    if payload is None:
        return Opened(None, reports=reports)
    signatures = envelope["signatures"] if isinstance(envelope["signatures"], list) else []
    if not signatures:
        reports.append(_violation("the envelope carries no signature", "$.signatures"))

    signers: set[str] = set()
    data = pae(str(envelope["payloadType"]), body)
    for index, signature in enumerate(signatures):
        path = f"$.signatures[{index}]"
        keyid = signature.get("keyid") if isinstance(signature, dict) else None
        key = keys.get(keyid) if isinstance(keyid, str) else None
        if key is None:
            message = f"key {keyid} is not trusted, so this signature cannot be checked"
            reports.append(Report(Level.WARNING, "signature", message, path))
        elif isinstance(keyid, str) and _verifies(key, signature.get("sig"), data):
            signers.add(keyid)
        else:
            reports.append(_violation(f"the signature by key {keyid} does not verify", path))

    unsigned = any(r.level == Level.VIOLATION for r in reports)
    return Opened(payload, Signers() if unsigned else Signers(keyids=frozenset(signers)), reports)


def _sign(key: PrivateKey, data: bytes) -> bytes:
    if isinstance(key, Ed25519PrivateKey):
        return key.sign(data)
    return key.sign(data, ec.ECDSA(hashes.SHA256()))


def _verifies(key: PublicKey, sig: object, data: bytes) -> bool:
    try:
        signature = base64.b64decode(str(sig), validate=True)
        if isinstance(key, Ed25519PublicKey):
            key.verify(signature, data)
        else:
            key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, binascii.Error, ValueError):
        return False
    return True


def _violation(message: str, path: str) -> Report:
    return Report(Level.VIOLATION, "signature", message, path)
