"""Integrity rules of spec section 5, checked on a bundle or passport that passed the schema.

How each rule is read, and which rules apply to which input, is fixed by the conformance fixtures
(fixtures/README.md in the specification repository). A check that needs the record behind an
unresolved reference skips it; R10 reports the reference once.
"""

from collections.abc import Callable, Iterator

from ois_validator import digests
from ois_validator.document import Document, Entry, Record
from ois_validator.report import Level, Report

Rule = Callable[[Document], Iterator[Report]]


def r4_derivation(document: Document) -> Iterator[Report]:
    """R4: a test derived from the intent saw the criterion and no code.

    Its context includes the criterion, or a version of the intent whose criterion with the same
    key has the same digest, and no artifact of kind code.
    """
    for entry, intent, required, test in _derived_from_intent(document):
        path = f"{entry.path}.derivedFrom"
        context = test.get("context", [])
        if not context:
            yield Report(Level.VIOLATION, "R4", f"test {test['id']} has no context", path)
            continue
        for reference in context:
            seen = document.record(reference)
            if seen is not None and seen["type"] == "artifact" and seen["kind"] == "code":
                message = f"the context of test {test['id']} includes code {reference}"
                yield Report(Level.VIOLATION, "R4", message, path)
        if not any(_covers(document, reference, intent, required) for reference in context):
            message = (
                f"the context of test {test['id']} includes neither {entry.record['criterion']} "
                "nor a version of the intent with the same criterion digest"
            )
            yield Report(Level.VIOLATION, "R4", message, path)


def r5_independence(document: Document) -> Iterator[Report]:
    """R5: a test derived from the intent was not produced by the actor that produced the
    subject, in any version."""
    for entry, _, _, test in _derived_from_intent(document):
        subject = document.record(entry.record.get("subject", ""))
        if subject is None or subject["type"] != "artifact":
            continue
        producer = _actor_id(test["producedBy"])
        if producer == _actor_id(subject["producedBy"]):
            message = f"test and subject are both produced by {producer}"
            yield Report(Level.VIOLATION, "R5", message, f"{entry.path}.derivedFrom")


def _derived_from_intent(document: Document) -> Iterator[tuple[Entry, Record, Record, Record]]:
    """Evidence with derivedFrom intent, with its intent version, criterion and test artifact,
    where all three resolve."""
    for entry in document.of_type("evidence"):
        if entry.record["derivedFrom"] != "intent":
            continue
        intent_ref, _, key = entry.record["criterion"].partition("#")
        intent = document.record(intent_ref)
        required = criterion(intent, key) if intent is not None else None
        test = document.record(entry.record["test"])
        if intent is None or required is None or test is None or test["type"] != "artifact":
            continue
        yield entry, intent, required, test


def _covers(document: Document, reference: str, intent: Record, required: Record) -> bool:
    """Whether a context entry gives the producer the required criterion (R4)."""
    base, _, rest = reference.partition("@")
    version, _, key = rest.partition("#")
    if base != intent["id"] or not version or key not in ("", required["key"]):
        return False
    if version == str(intent["version"]):
        return True
    other = document.record(f"{base}@{version}")
    seen = criterion(other, required["key"]) if other is not None else None
    return seen is not None and digests.equal(seen.get("digest", {}), required.get("digest", {}))


def _actor_id(reference: str) -> str:
    return reference.partition("@")[0]


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


def r1_approval(document: Document) -> Iterator[Report]:
    """R1: an approved intent has an approved intent-approval decision bound to its digest.

    The approving actor SHOULD be human (info).
    """
    for entry in document.of_type("intent"):
        intent = entry.record
        if intent["status"] != "approved":
            continue
        subject = f"{intent['id']}@{intent['version']}"
        approvals = [
            decision
            for decision in document.of_type("decision")
            if decision.record["gate"] == "intent-approval"
            and decision.record["subject"] == subject
            and decision.record["outcome"] == "approved"
            and digests.equal(decision.record.get("subjectDigest", {}), intent["digest"])
        ]
        if not approvals:
            message = (
                f"{subject} is approved, but no intent-approval decision approves it "
                "with a subjectDigest equal to its digest"
            )
            yield Report(Level.VIOLATION, "R1", message, entry.path)
        for decision in approvals:
            decided_by = decision.record["decidedBy"]
            if _is_human(document, decided_by) is False:
                message = f"{subject} SHOULD be approved by a human, not {decided_by}"
                yield Report(Level.INFO, "R1", message, f"{decision.path}.decidedBy")


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


def r17_digests(document: Document) -> Iterator[Report]:
    """R17 (proposed): intent and criterion digests match their content (section 6.4)."""
    for entry in document.of_type("intent"):
        intent = entry.record
        for index, item in enumerate(intent["criteria"]):
            stored = item.get("digest", {}).get("sha256")
            if stored is not None and stored != digests.criterion_sha256(item):
                message = f"criterion {item['key']!r}: sha256 does not match its content"
                yield Report(
                    Level.VIOLATION, "R17", message, f"{entry.path}.criteria[{index}].digest"
                )
        stored = intent.get("digest", {}).get("sha256")
        if stored is not None and stored != digests.intent_sha256(intent):
            message = "sha256 does not match the intent's content"
            yield Report(Level.VIOLATION, "R17", message, f"{entry.path}.digest")


def criterion(intent: Record, key: str) -> Record | None:
    return next((c for c in intent["criteria"] if c["key"] == key), None)


def _is_human(document: Document, reference: str) -> bool | None:
    """Whether a reference names a human actor; None if it does not resolve to an actor."""
    actor = document.record(reference)
    if actor is None or actor["type"] != "actor":
        return None
    return bool(actor["kind"] == "human")


def _kind(record: Record) -> str:
    return f"{record['type']} of kind {record['kind']}" if "kind" in record else record["type"]


def _content(record: Record) -> Record:
    """What R10 compares. A draft intent and its approved record differ only in status and
    digest, and an intent's digest follows from its content, so intents leave both out. For
    every other record the digest pins the content and is compared."""
    if record["type"] != "intent":
        return record
    return {k: v for k, v in record.items() if k not in ("status", "digest")}


RECORD_RULES: tuple[Rule, ...] = (r17_digests,)  # also for a single record
CONTAINER_RULES: tuple[Rule, ...] = (
    r1_approval,
    r2_criterion_references,
    r3_evidence_targets,
    r4_derivation,
    r5_independence,
    r10_resolution,
)  # bundles and passports only


def check(document: Document) -> Iterator[Report]:
    for rule in RECORD_RULES:
        yield from rule(document)
    if document.container:
        for rule in CONTAINER_RULES:
            yield from rule(document)
