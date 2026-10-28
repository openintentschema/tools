"""Validate a parsed OIS document: its signatures if it is a DSSE envelope, then Appendix B, then
the integrity rules of section 5."""

from collections.abc import Iterable

from ois_tools import rules, schema
from ois_tools.document import Document
from ois_tools.report import Result
from ois_tools.signing import TrustedKey, is_envelope, open_envelope


def validate(document: object, *, trusted_keys: Iterable[TrustedKey] = ()) -> Result:
    """Check a record, a bundle, a Quality Passport statement, or a DSSE envelope around a bundle
    or passport, whose signatures are verified with `trusted_keys`.

    The integrity rules run only on input that passed the schema, so they can rely on its
    structure. Rules that relate records to each other apply to bundles and passports only. Paths
    of reports on the payload are relative to the payload.
    """
    signers: frozenset[str] = frozenset()
    reports = []
    if is_envelope(document):
        assert isinstance(document, dict)
        opened = open_envelope(document, trusted_keys)
        if opened.payload is None:
            return Result(opened.reports)
        document, signers, reports = opened.payload, opened.signers, opened.reports
    result = schema.check(document)
    if result.conforms and isinstance(document, dict):
        result.reports.extend(rules.check(Document.of(document, signers=signers)))
    return Result([*reports, *result.reports])
