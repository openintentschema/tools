"""Validate a parsed OIS document: Appendix B first, then the integrity rules of section 5."""

from ois_validator import rules, schema
from ois_validator.document import Document
from ois_validator.report import Result


def validate(document: object) -> Result:
    """Check a record, a bundle or a Quality Passport statement.

    The integrity rules run only on input that passed the schema, so they can rely on its
    structure. They relate records to each other and therefore apply to bundles and passports.
    """
    result = schema.check(document)
    if result.conforms:
        container = Document.of(document)
        if container is not None:
            result.reports.extend(rules.check(container))
    return result
