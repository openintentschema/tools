"""Validate a parsed OIS document: Appendix B first, then the integrity rules of section 5."""

from ois_tools import rules, schema
from ois_tools.document import Document
from ois_tools.report import Result


def validate(document: object) -> Result:
    """Check a record, a bundle or a Quality Passport statement.

    The integrity rules run only on input that passed the schema, so they can rely on its
    structure. Rules that relate records to each other apply to bundles and passports only.
    """
    result = schema.check(document)
    if result.conforms and isinstance(document, dict):
        result.reports.extend(rules.check(Document.of(document)))
    return result
