"""Validate a parsed OIS document: its signatures if it is a DSSE envelope, then Appendix B, then
the integrity rules of section 5."""

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
    """Check a record, a bundle, a Quality Passport statement, a DSSE envelope around a bundle or
    passport (signatures verified with `trusted_keys`), or a Sigstore bundle (verified against
    `trusted_roots`, see `keyless.load_trusted_roots`).

    The integrity rules run only on input that passed the schema, so they can rely on its
    structure. Rules that relate records to each other apply to bundles and passports only. Paths
    of reports on the payload are relative to the payload.
    """
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
