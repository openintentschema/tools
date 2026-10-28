"""Integrity rules of spec section 5, checked on a bundle or passport that passed the schema.

How each rule is read, and which rules apply to which input, is fixed by the conformance fixtures
(fixtures/README.md in the specification repository). A check that needs the record behind an
unresolved reference skips it; R10 reports the reference once.
"""

from collections.abc import Callable, Iterator

from ois_validator.document import Document, Record
from ois_validator.report import Level, Report

Rule = Callable[[Document], Iterator[Report]]


def r10_resolution(document: Document) -> Iterator[Report]:
    """R10: every reference resolves to exactly one record; no conflicting duplicates."""
    for reference in document.references():
        if not document.resolve(reference.value):
            yield Report(
                Level.VIOLATION, "R10", f"{reference.value} resolves to no record", reference.path
            )
    seen: dict[tuple[str, str], tuple[Record, str]] = {}
    for entry in document.entries:
        key = (entry.record["id"], str(entry.record.get("version", "")))
        if key not in seen:
            seen[key] = (_content(entry.record), entry.path)
        elif seen[key][0] != _content(entry.record):
            message = f"same id as {seen[key][1]}, but different content"
            yield Report(Level.VIOLATION, "R10", message, entry.path)


def r2_criterion_references(document: Document) -> Iterator[Report]:
    """R2: a criterion reference names a key that exists in that intent version."""
    for reference in document.references():
        intent_ref, _, key = reference.value.partition("#")
        intent = document.record(intent_ref) if key else None
        if intent is not None and criterion(intent, key) is None:
            message = f"{intent_ref} has no criterion {key!r}"
            yield Report(Level.VIOLATION, "R2", message, reference.path)


def r3_evidence_targets(document: Document) -> Iterator[Report]:
    """R3: evidence names an artifact of kind test as test, and code or build as subject."""
    for entry in document.of_type("evidence"):
        for name, kinds in (("test", ("test",)), ("subject", ("code", "build"))):
            if name not in entry.record:
                continue
            target = document.record(entry.record[name])
            if target is not None and (target["type"] != "artifact" or target["kind"] not in kinds):
                expected = " or ".join(kinds)
                message = f"{name} must be an artifact of kind {expected}, not {_kind(target)}"
                yield Report(Level.VIOLATION, "R3", message, f"{entry.path}.{name}")


def criterion(intent: Record, key: str) -> Record | None:
    return next((c for c in intent["criteria"] if c["key"] == key), None)


def _kind(record: Record) -> str:
    return f"{record['type']} of kind {record['kind']}" if "kind" in record else record["type"]


def _content(record: Record) -> Record:
    """What R10 compares. A draft intent and its approved record differ only in status and
    digest, and an intent's digest follows from its content, so intents leave both out. For
    every other record the digest pins the content and is compared."""
    if record["type"] != "intent":
        return record
    return {k: v for k, v in record.items() if k not in ("status", "digest")}


RECORD_RULES: tuple[Rule, ...] = ()  # also for a single record
CONTAINER_RULES: tuple[Rule, ...] = (
    r2_criterion_references,
    r3_evidence_targets,
    r10_resolution,
)  # bundles and passports only


def check(document: Document) -> Iterator[Report]:
    for rule in RECORD_RULES:
        yield from rule(document)
    if document.container:
        for rule in CONTAINER_RULES:
            yield from rule(document)
