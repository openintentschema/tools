"""Validate a parsed OIS document: signatures, then Appendix B, then the rules of section 5."""

from collections.abc import Iterable
from typing import Any

from ois_tools import profiles as profile_rules
from ois_tools import rules, schema
from ois_tools.document import Document, Signers
from ois_tools.keyless import is_sigstore_bundle, open_sigstore_bundle
from ois_tools.report import Result
from ois_tools.signing import TrustedKey, is_envelope, open_envelope


def validate(
    document: object,
    *,
    trusted_keys: Iterable[TrustedKey] = (),
    trusted_roots: Iterable[Any] = (),
    profiles: Iterable[str] = (),
) -> Result:
    """Check a record, bundle, passport, DSSE envelope (`trusted_keys`) or Sigstore bundle
    (`trusted_roots`), and the profiles its records list or `profiles` names (URIs). Integrity
    rules run only after the schema passes; report paths on a payload are relative to it."""
    signers = Signers()
    enveloped = False
    reports = []
    if is_envelope(document) or is_sigstore_bundle(document):
        assert isinstance(document, dict)
        if is_envelope(document):
            opened = open_envelope(document, trusted_keys)
            enveloped = bool(document["signatures"])
        else:
            opened = open_sigstore_bundle(document, trusted_roots)
            enveloped = bool(document.get("dsseEnvelope", {}).get("signatures"))
        if opened.payload is None:
            return Result(opened.reports)
        document, signers, reports = opened.payload, opened.signers, opened.reports
    result = schema.check(document)
    if result.conforms and isinstance(document, dict):
        parsed = Document.of(document, signers=signers, enveloped=enveloped)
        result.reports.extend(rules.check(parsed))
        result.reports.extend(profile_rules.check(parsed, profiles))
    return Result([*reports, *result.reports])
