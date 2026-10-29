"""Intent coverage by the reference method of Appendix D and its variants. Several listed intents
are counted together (section 8.2)."""

import operator
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ois_tools import digests
from ois_tools.document import NOBODY, Document, Entry, Signers
from ois_tools.keyless import is_sigstore_bundle, open_sigstore_bundle
from ois_tools.rules import criterion as criterion_of
from ois_tools.rules import self_declared
from ois_tools.schema import STATEMENT_TYPE
from ois_tools.signing import TrustedKey, is_envelope, open_envelope

Record = dict[str, Any]

METHOD = "https://ois.example/methods/basic-intent-coverage/v0.1"  # placeholder until RFC #6
NAMES = (
    "intentCoverage",
    "intentCoverageAttested",
    "intentCoverageWeighted",
    "intentCoverageDiverse",
)
WEIGHTS = {"high": 3, "medium": 2, "low": 1}
OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "lt": operator.lt,
    "lte": operator.le,
    "gt": operator.gt,
    "gte": operator.ge,
    "eq": operator.eq,
}


class CoverageError(ValueError):
    """Coverage cannot be computed from these records."""


@dataclass(frozen=True)
class Criterion:
    reference: str  # <intent>@<version>#<key>
    statement: str
    weight: int
    counted: tuple[str, ...]  # evidence ids that count
    attested: tuple[str, ...]  # of those, the ones whose test context is not self-declared
    diverse: tuple[str, ...]  # of those, the ones with diverse producers
    reasons: tuple[tuple[str, str], ...]  # (evidence id, why it does not count)

    @property
    def covered(self) -> bool:
        return bool(self.counted)


@dataclass(frozen=True)
class Coverage:
    criteria: tuple[Criterion, ...]

    def measurements(self) -> list[Record]:
        """intentCoverage and its variants, rounded to four decimals; none without criteria."""
        if not self.criteria:
            return []
        total = sum(c.weight for c in self.criteria)
        fractions = (
            _share(self.criteria, lambda c: bool(c.counted)),
            _share(self.criteria, lambda c: bool(c.attested)),
            sum(c.weight for c in self.criteria if c.counted) / total,
            _share(self.criteria, lambda c: bool(c.diverse)),
        )
        return [
            {"name": name, "value": round(value, 4), "method": METHOD}
            for name, value in zip(NAMES, fractions, strict=True)
        ]


def passport_coverage(
    statement: Record,
    trusted_keys: Iterable[TrustedKey] = (),
    trusted_roots: Iterable[Any] = (),
) -> Coverage:
    """Coverage of a passport: its intents and scope, for the build its release decision names.
    A signed passport (DSSE envelope or Sigstore bundle) counts its verified signers for the
    attested variant (R12)."""
    signers = Signers()
    if is_envelope(statement):
        opened = open_envelope(statement, trusted_keys)
        statement, signers = opened.payload, opened.signers
    elif is_sigstore_bundle(statement):
        opened = open_sigstore_bundle(statement, trusted_roots)
        statement, signers = opened.payload, opened.signers
    if not isinstance(statement, dict) or statement.get("_type") != STATEMENT_TYPE:
        raise CoverageError("not a passport: expected an in-toto Statement")
    predicate = statement["predicate"]
    document = _document(predicate["records"])
    subjects = [s["digest"] for s in statement.get("subject", []) if isinstance(s, dict)]
    build = release_subject(document, subjects)
    if build is None:
        raise CoverageError(
            "no release decision approves a build whose digest matches the statement subject (R16)"
        )
    return coverage(
        predicate["records"], subject=build["id"], intents=predicate["intents"],
        scope=predicate.get("scope"), signers=signers,
    )  # fmt: skip


def release_subject(document: Document, subjects: list[dict[str, str]]) -> Record | None:
    """The build artifact of an approved or passed release decision that matches a subject."""
    for entry in document.of_type("decision"):
        decision = entry.record
        if decision["gate"] != "release" or decision["outcome"] not in ("approved", "passed"):
            continue
        build = document.record(decision["subject"])
        if (
            build is not None
            and build["type"] == "artifact"
            and build["kind"] == "build"
            and any(digests.equal(build["digest"], subject) for subject in subjects)
        ):
            return build
    return None


def coverage(
    records: Iterable[Record],
    *,
    subject: str,
    intents: list[str],
    scope: list[str] | None = None,
    signers: Signers = NOBODY,
) -> Coverage:
    """Coverage of the listed intent versions for the release subject `subject` (an artifact id).
    `signers` signed the document carrying the records (R12)."""
    document = _document(list(records), signers)
    release = document.record(subject)
    if release is None or release["type"] != "artifact":
        raise CoverageError(f"the release subject {subject} is not an artifact among the records")
    criteria = []
    for listed in intents:
        intent = document.record(listed)
        if intent is None or intent["type"] != "intent":
            continue
        reference = f"{intent['id']}@{intent['version']}"
        for required in intent["criteria"]:
            if scope is not None and f"{reference}#{required['key']}" not in scope:
                continue
            criteria.append(_criterion(document, intent, required, release))
    return Coverage(tuple(criteria))


def _criterion(document: Document, intent: Record, required: Record, release: Record) -> Criterion:
    counted, attested, diverse, reasons = [], [], [], []
    for entry in document.of_type("evidence"):
        evidence = entry.record
        why = _relevance(document, evidence, intent, required)
        if why == "":
            continue  # evidence for another criterion
        why = why or _reason(document, evidence, required, release)
        if why is not None:
            reasons.append((evidence["id"], why))
            continue
        counted.append(evidence["id"])
        test = document.record(evidence["test"]) or {}
        if self_declared(test, document) is None:
            attested.append(evidence["id"])
        if _diverse(document, test, document.record(evidence["subject"]) or {}):
            diverse.append(evidence["id"])
    return Criterion(
        reference=f"{intent['id']}@{intent['version']}#{required['key']}",
        statement=required["statement"],
        weight=WEIGHTS.get(required.get("criticality", ""), 1),
        counted=tuple(counted),
        attested=tuple(attested),
        diverse=tuple(diverse),
        reasons=tuple(reasons),
    )


def _relevance(
    document: Document, evidence: Record, intent: Record, required: Record
) -> str | None:
    """None if the evidence is for this criterion now; why not if it is for the same criterion in
    another version; "" if it concerns another criterion."""
    base, _, rest = evidence["criterion"].partition("@")
    version, _, key = rest.partition("#")
    if base != intent["id"] or key != required["key"]:
        return ""
    if version == str(intent["version"]):
        return None
    if int(version) > intent["version"]:
        return f"for the later version {version}"
    earlier = document.record(f"{base}@{version}")
    seen = criterion_of(earlier, key) if earlier is not None else None
    if seen is None or not digests.equal(seen.get("digest", {}), required.get("digest", {})):
        return f"stale: the criterion changed since version {version}"
    return None


def _reason(document: Document, evidence: Record, required: Record, release: Record) -> str | None:
    """Why counted-evidence condition of Appendix D fails, or None if all hold."""
    if evidence["result"] != "pass":
        return f"result is {evidence['result']}"
    if evidence["derivedFrom"] != "intent":
        return f"derivedFrom is {evidence['derivedFrom']}"
    subject = evidence.get("subject")
    if subject is None:
        return "no subject"
    if subject != release["id"] and subject not in release.get("inputs", []):
        return f"subject {subject} is neither the release subject nor one of its inputs"
    for entry in document.of_type("decision"):
        if (
            entry.record["gate"] == "evidence-revocation"
            and entry.record["subject"] == evidence["id"]
        ):
            return f"revoked by {entry.record['id']}"
    produced = document.record(subject)
    if produced is None:
        return f"subject {subject} is not among the records"
    if _time(evidence["observedAt"]) <= _time(produced["createdAt"]):
        return (
            f"observed at {evidence['observedAt']}, not after its subject was created at "
            f"{produced['createdAt']}"
        )
    target = required.get("target")
    if target is not None and not _meets(evidence.get("measures", []), target):
        return f"no measure {target['metric']} {target['operator']} {target['value']}"
    return None


def _meets(measures: list[Record], target: Record) -> bool:
    compare = OPERATORS[target["operator"]]
    return any(
        m["name"] == target["metric"]
        and isinstance(m["value"], int | float)
        and not isinstance(m["value"], bool)
        and compare(m["value"], target["value"])
        for m in measures
    )


def _diverse(document: Document, test: Record, subject: Record) -> bool:
    """At least one producer is human, or both are agents whose models share no provider and
    name."""
    producers = [document.record(r.get("producedBy", "")) for r in (test, subject)]
    if any(p is None or p["type"] != "actor" for p in producers):
        return False
    first, second = producers[0] or {}, producers[1] or {}
    if "human" in (first["kind"], second["kind"]):
        return True
    if first["kind"] != "agent" or second["kind"] != "agent":
        return False
    return not any(
        (a["provider"], a["name"]) == (b["provider"], b["name"])
        for a in _models(first)
        for b in _models(second)
    )


def _models(agent: Record) -> list[Record]:
    model = agent["model"]
    return model if isinstance(model, list) else [model]


def _share(criteria: tuple[Criterion, ...], covered: Callable[[Criterion], bool]) -> float:
    return sum(1 for c in criteria if covered(c)) / len(criteria)


def _document(records: list[Record], signers: Signers = NOBODY) -> Document:
    entries = [Entry(record, f"$.records[{i}]") for i, record in enumerate(records)]
    return Document(entries, signers=signers)


def _time(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)
