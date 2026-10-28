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
    """The records of a single record, a bundle or a passport, after the schema check passed."""

    entries: list[Entry]
    container: bool = True  # False for a single record: no rules across records apply
    passport: Record | None = None  # the predicate, for passports
    subjects: list[dict[str, str]] = field(default_factory=list)  # statement subject digests
    _by_id: dict[str, list[Entry]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for entry in self.entries:
            self._by_id.setdefault(entry.record["id"], []).append(entry)

    @classmethod
    def of(cls, document: Record) -> "Document":
        if document.get("_type") == STATEMENT_TYPE:
            predicate: Record = document["predicate"]
            # Appendix B covers only the predicate, so the statement's subject is read defensively.
            subject = document.get("subject")
            subjects = [
                s["digest"]
                for s in (subject if isinstance(subject, list) else [])
                if isinstance(s, dict) and isinstance(s.get("digest"), dict)
            ]
            entries = _entries(predicate["records"], "$.predicate.records")
            return cls(entries, passport=predicate, subjects=subjects)
        if document["type"] == "bundle":
            return cls(_entries(document["records"], "$.records"))
        return cls([Entry(document, "$")], container=False)

    def resolve(self, reference: str) -> list[Entry]:
        """Every record a reference names; more than one only for records in several states."""
        base, _, rest = reference.partition("@")
        version = rest.partition("#")[0]
        candidates = self._by_id.get(base, [])
        if version:
            return [e for e in candidates if str(e.record.get("version")) == version]
        return [e for e in candidates if "version" not in e.record]

    def record(self, reference: str) -> Record | None:
        """The record a reference names, or None if it does not resolve."""
        found = self.resolve(reference)
        return found[0].record if found else None

    def of_type(self, kind: str) -> Iterator[Entry]:
        return (entry for entry in self.entries if entry.record["type"] == kind)

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
