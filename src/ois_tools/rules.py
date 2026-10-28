"""Integrity rules of spec section 5, checked on input that passed the schema.

How each rule is read, and which rules apply to which input, is fixed by the conformance fixtures
(fixtures/README.md in the specification repository). A check that needs the record behind an
unresolved reference skips it; R10 reports the reference once.
"""

from collections.abc import Callable, Iterator
from datetime import datetime

from ois_tools import digests
from ois_tools.document import Document, Entry, Record
from ois_tools.report import Level, Report

Rule = Callable[[Document], Iterator[Report]]


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


def r8_production_data(document: Document) -> Iterator[Report]:
    """R8: evidence that uses a production dataset is reported (warning)."""
    for entry in document.of_type("evidence"):
        for index, reference in enumerate(entry.record.get("inputs", [])):
            dataset = document.record(reference)
            if (
                dataset is not None
                and dataset["type"] == "artifact"
                and dataset.get("classification") == "production"
            ):
                message = f"input {reference} is a dataset classified as production"
                yield Report(Level.WARNING, "R8", message, f"{entry.path}.inputs[{index}]")


def r9_waivers(document: Document) -> Iterator[Report]:
    """R9: a waiver SHOULD be made by a human (info). The rationale is in Appendix B."""
    for entry in document.of_type("decision"):
        decided_by = entry.record["decidedBy"]
        if entry.record["outcome"] == "waived" and _is_human(document, decided_by) is False:
            message = f"a waiver SHOULD be made by a human, not {decided_by}"
            yield Report(Level.INFO, "R9", message, f"{entry.path}.decidedBy")


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
            seen[key] = (content(entry.record), entry.path)
        elif seen[key][0] != content(entry.record):
            message = f"same id as {seen[key][1]}, but different content"
            yield Report(Level.VIOLATION, "R10", message, entry.path)


def r11_revocation(document: Document) -> Iterator[Report]:
    """R11: an evidence-revocation decision revokes evidence. Rationale and outcome are in
    Appendix B."""
    for entry in document.of_type("decision"):
        if entry.record["gate"] != "evidence-revocation":
            continue
        subject = document.record(entry.record["subject"])
        if subject is not None and subject["type"] != "evidence":
            message = f"evidence-revocation must have evidence as subject, not {_kind(subject)}"
            yield Report(Level.VIOLATION, "R11", message, f"{entry.path}.subject")


def r12_context_attestation(document: Document) -> Iterator[Report]:
    """R12: a context counts as attested only if another actor recorded it and signed the bundle
    or passport that carries it. Otherwise it is self-declared (warning).

    Signatures are not verified yet, so every context is reported as self-declared.
    """
    for entry in document.of_type("artifact"):
        reason = self_declared(entry.record)
        if reason is not None:
            message = f"context is self-declared: {reason}"
            yield Report(Level.WARNING, "R12", message, f"{entry.path}.context")


def r13_open_findings(document: Document) -> Iterator[Report]:
    """R13: a passport lists an intent that a current, unresolved high or critical finding
    references directly or through a criterion (warning)."""
    if document.passport is None:
        return
    findings = list(document.of_type("finding"))
    superseded = {f.record["supersedes"] for f in findings if "supersedes" in f.record}
    for index, listed in enumerate(document.passport["intents"]):
        for entry in findings:
            finding = entry.record
            if finding["id"] in superseded or finding["severity"] not in ("high", "critical"):
                continue
            concerns = finding.get("intent") == listed or (
                finding.get("criterion", "").partition("#")[0] == listed
            )
            if concerns and not _resolved(document, finding):
                message = f"{listed} has an open {finding['severity']} finding {finding['id']}"
                yield Report(Level.WARNING, "R13", message, f"$.predicate.intents[{index}]")


def r14_finding_chain(document: Document) -> Iterator[Report]:
    """R14: a finding has at most one superseding finding."""
    first: dict[str, str] = {}
    for entry in document.of_type("finding"):
        previous = entry.record.get("supersedes")
        if previous is None:
            continue
        if previous in first:
            message = f"{previous} is already superseded by the finding at {first[previous]}"
            yield Report(Level.VIOLATION, "R14", message, f"{entry.path}.supersedes")
        else:
            first[previous] = entry.path


def r15_passport_intents(document: Document) -> Iterator[Report]:
    """R15: a passport lists approved intent versions that are not withdrawn, and SHOULD list the
    latest version approved before issuedAt (info)."""
    if document.passport is None:
        return
    issued_at = _time(document.passport["issuedAt"])
    for index, listed in enumerate(document.passport["intents"]):
        intent = document.record(listed)
        if intent is None or intent["type"] != "intent":
            continue
        path = f"$.predicate.intents[{index}]"
        later = [
            e.record
            for e in document.of_type("intent")
            if e.record["id"] == intent["id"] and e.record["version"] > intent["version"]
        ]
        withdrawn = [r["version"] for r in later if r["status"] == "withdrawn"]
        if intent["status"] != "approved":
            message = f"{listed} is {intent['status']}, not approved"
            yield Report(Level.VIOLATION, "R15", message, path)
        elif withdrawn:
            message = f"{listed} is withdrawn by version {min(withdrawn)}"
            yield Report(Level.VIOLATION, "R15", message, path)
        elif newer := [r["version"] for r in later if _approved_before(document, r, issued_at)]:
            message = f"{listed} SHOULD be the latest approved version, which is {max(newer)}"
            yield Report(Level.INFO, "R15", message, path)


def r16_release_decision(document: Document) -> Iterator[Report]:
    """R16 (proposed): a passport carries an approved or passed release decision for a build
    whose digest matches a subject of the statement."""
    if document.passport is None:
        return
    for entry in document.of_type("decision"):
        decision = entry.record
        if decision["gate"] != "release" or decision["outcome"] not in ("approved", "passed"):
            continue
        build = document.record(decision["subject"])
        if (
            build is not None
            and build["type"] == "artifact"
            and build["kind"] == "build"
            and any(digests.equal(build["digest"], subject) for subject in document.subjects)
        ):
            return
    message = (
        "no release decision with outcome approved or passed for a build "
        "whose digest matches a subject of the statement"
    )
    yield Report(Level.VIOLATION, "R16", message, "$.predicate.records")


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


RECORD_RULES: tuple[Rule, ...] = (r17_digests,)  # also for a single record
CONTAINER_RULES: tuple[Rule, ...] = (
    r1_approval,
    r2_criterion_references,
    r3_evidence_targets,
    r4_derivation,
    r5_independence,
    r8_production_data,
    r9_waivers,
    r10_resolution,
    r11_revocation,
    r12_context_attestation,
    r13_open_findings,
    r14_finding_chain,
    r15_passport_intents,
    r16_release_decision,
)  # bundles and passports only


def check(document: Document) -> Iterator[Report]:
    for rule in RECORD_RULES:
        yield from rule(document)
    if document.container:
        for rule in CONTAINER_RULES:
            yield from rule(document)


def self_declared(artifact: Record) -> str | None:
    """R12: why an artifact's context is self-declared, or None if it has no context. Signatures
    are not verified yet, so no context counts as attested."""
    if "context" not in artifact:
        return None
    recorder = artifact.get("contextRecordedBy")
    if recorder is None:
        return "no contextRecordedBy"
    if _actor_id(recorder) == _actor_id(artifact["producedBy"]):
        return "recorded by its own producer"
    return f"no verified signature of {recorder}"


def criterion(record: Record, key: str) -> Record | None:
    """The criterion with that key, if the record is an intent that has one."""
    if record["type"] != "intent":
        return None
    return next((c for c in record["criteria"] if c["key"] == key), None)


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


def _is_human(document: Document, reference: str) -> bool | None:
    """Whether a reference names a human actor; None if it does not resolve to an actor."""
    actor = document.record(reference)
    if actor is None or actor["type"] != "actor":
        return None
    return bool(actor["kind"] == "human")


def _resolved(document: Document, finding: Record) -> bool:
    """R14: an approved finding-resolution decision for the finding, by an actor that produced,
    recorded or decided none of the records in its resolvedBy."""
    involved = set()
    for reference in finding.get("resolvedBy", []):
        record = document.record(reference)
        for name in ("producedBy", "recordedBy", "decidedBy"):
            if record is not None and name in record:
                involved.add(_actor_id(record[name]))
    return any(
        d.record["gate"] == "finding-resolution"
        and d.record["outcome"] == "approved"
        and d.record["subject"] == finding["id"]
        and _actor_id(d.record["decidedBy"]) not in involved
        for d in document.of_type("decision")
    )


def _approved_before(document: Document, intent: Record, moment: datetime) -> bool:
    """Whether an intent version is approved by an intent-approval decision made before moment."""
    subject = f"{intent['id']}@{intent['version']}"
    return intent["status"] == "approved" and any(
        d.record["gate"] == "intent-approval"
        and d.record["outcome"] == "approved"
        and d.record["subject"] == subject
        and _time(d.record["decidedAt"]) < moment
        for d in document.of_type("decision")
    )


def _time(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def _kind(record: Record) -> str:
    return f"{record['type']} of kind {record['kind']}" if "kind" in record else record["type"]


def content(record: Record) -> Record:
    """What R10 compares. A draft intent and its approved record differ only in status and
    digest, and an intent's digest follows from its content, so intents leave both out. For
    every other record the digest pins the content and is compared."""
    if record["type"] != "intent":
        return record
    return {k: v for k, v in record.items() if k not in ("status", "digest")}
