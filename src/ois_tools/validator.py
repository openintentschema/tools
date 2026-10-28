"""Validate a parsed OIS document: signatures, then Appendix B, then the rules of section 5."""

from collections.abc import Iterable
from typing import Any

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
) -> Result:
    """Check a record, bundle, passport, DSSE envelope (`trusted_keys`) or Sigstore bundle
    (`trusted_roots`). Integrity rules run only after the schema passes; report paths on a
    payload are relative to it."""
    signers = Signers()
    reports = []
    if is_envelope(document) or is_sigstore_bundle(document):
        assert isinstance(document, dict)
        if is_envelope(document):
            opened = open_envelope(document, trusted_keys)
        else:
            opened = open_sigstore_bundle(document, trusted_roots)
        if opened.payload is None:
            return Result(opened.reports)
        document, signers, reports = opened.payload, opened.signers, opened.reports
    result = schema.check(document)
    if result.conforms and isinstance(document, dict):
        result.reports.extend(rules.check(Document.of(document, signers=signers)))
    return Result([*reports, *result.reports])
