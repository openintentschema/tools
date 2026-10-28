"""The records of a bundle or passport and how references between them resolve.

Resolution follows the conformance fixtures (fixtures/README.md in the specification repository):
`<id>@<version>` resolves to the intent or actor with that id and version, `<id>` to a record
with that id and no version, and `<id>@<version>#<key>` to the intent version (the key is R2's
concern). Only Ref-typed fields are references.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ois_validator.schema import STATEMENT_TYPE

Record = dict[str, Any]

REFERENCE_FIELDS = {
    "intent": ("producedBy", "supersedes"),
    "artifact": ("producedBy", "inputs", "context", "contextRecordedBy"),
    "actor": (),
    "evidence": ("criterion", "test", "subject", "inputs", "recordedBy"),
    "decision": ("subject", "decidedBy", "evidence"),
    "finding": ("criterion", "intent", "subject", "reportedBy", "resolvedBy", "supersedes"),
}
PASSPORT_REFERENCE_FIELDS = ("intents", "scope", "issuedBy")


@dataclass(frozen=True)
class Entry:
    record: Record
    path: str  # JSONPath of the record in the input document


@dataclass(frozen=True)
class Reference:
    value: str
    path: str  # JSONPath of the reference in the input document


@dataclass
class Document:
    """A bundle or the predicate of a passport, after the schema check has passed."""

    entries: list[Entry]
    passport: Record | None = None  # the predicate, for passports
    _by_id: dict[str, list[Entry]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for entry in self.entries:
            self._by_id.setdefault(entry.record["id"], []).append(entry)

    @classmethod
    def of(cls, document: object) -> "Document | None":
        """The records of a bundle or passport, or None for a single record."""
        if not isinstance(document, dict):
            return None
        if document.get("_type") == STATEMENT_TYPE:
            predicate: Record = document["predicate"]
            return cls(_entries(predicate["records"], "$.predicate.records"), predicate)
        if document.get("type") == "bundle":
            return cls(_entries(document["records"], "$.records"))
        return None

    def resolve(self, reference: str) -> list[Entry]:
        """Every record a reference names; more than one only for records in several states."""
        base, _, rest = reference.partition("@")
        version = rest.partition("#")[0]
        candidates = self._by_id.get(base, [])
        if version:
            return [e for e in candidates if str(e.record.get("version")) == version]
        return [e for e in candidates if "version" not in e.record]

    def references(self) -> Iterator[Reference]:
        """Every reference in the document, in document order."""
        for entry in self.entries:
            yield from _references(entry.record, REFERENCE_FIELDS[entry.record["type"]], entry.path)
        if self.passport is not None:
            yield from _references(self.passport, PASSPORT_REFERENCE_FIELDS, "$.predicate")


def _entries(records: list[Record], path: str) -> list[Entry]:
    return [Entry(record, f"{path}[{index}]") for index, record in enumerate(records)]


def _references(record: Record, fields: tuple[str, ...], path: str) -> Iterator[Reference]:
    for name in fields:
        value = record.get(name)
        if isinstance(value, str):
            yield Reference(value, f"{path}.{name}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield Reference(item, f"{path}.{name}[{index}]")
