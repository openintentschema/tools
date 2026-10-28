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


def _content(record: Record) -> Record:
    """What R10 compares. A draft intent and its approved record differ only in status and
    digest, and an intent's digest follows from its content, so intents leave both out. For
    every other record the digest pins the content and is compared."""
    if record["type"] != "intent":
        return record
    return {k: v for k, v in record.items() if k not in ("status", "digest")}


RULES: tuple[Rule, ...] = (r10_resolution,)


def check(document: Document) -> Iterator[Report]:
    for rule in RULES:
        yield from rule(document)
