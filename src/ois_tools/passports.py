"""Assemble a Quality Passport (specification section 8) from the records behind a release.

The passport is an in-toto Statement v1: its subject is the released build, its predicate lists
the intents the release delivers, embeds every record it relies on and reports intent coverage by
the reference method of Appendix D. It is validated before it is returned; signing (DSSE) is a
separate step.
"""

import copy
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ois_tools.coverage import coverage
from ois_tools.rules import content
from ois_tools.schema import PASSPORT_PREDICATE_TYPE, STATEMENT_TYPE
from ois_tools.signing import is_envelope, open_envelope
from ois_tools.validator import validate

Record = dict[str, Any]


class PassportError(ValueError):
    """The records cannot make a conforming passport."""


@dataclass(frozen=True)
class Release:
    name: str
    version: str
    location: str | None = None


def collect(documents: Iterable[object]) -> list[Record]:
    """The records of single records, bundles and passports, each once.

    Identical copies are merged; of a draft and its approved record, the approved one stays. Two
    records with the same id (and version) but different content break R10.
    """
    result: list[Record] = []
    position: dict[tuple[str, str], int] = {}
    for document in documents:
        for record in _records(document):
            key = (record["id"], str(record.get("version", "")))
            if key not in position:
                position[key] = len(result)
                result.append(record)
                continue
            kept = result[position[key]]
            if content(kept) != content(record):
                raise PassportError(f"R10: two records {key[0]} differ in content")
            if record.get("status") == "approved" and kept.get("status") != "approved":
                result[position[key]] = record
    return result


def build(
    records: list[Record],
    *,
    release: Release,
    build: str,
    issued_by: str,
    issued_at: datetime,
    intents: list[str] | None = None,
    scope: list[str] | None = None,
    subject_name: str | None = None,
    approve_release_by: str | None = None,
    release_outcome: str = "approved",
    release_decision_id: str | None = None,
    assess_coverage: bool = True,
    signers: frozenset[str] = frozenset(),
) -> Record:
    """A conforming passport, not yet signed, for the release of `build`, with its intent
    coverage by the reference method of Appendix D unless `assess_coverage` is false. `signers`
    are the key ids that will sign it, which the attested coverage counts (R12)."""
    records = copy.deepcopy(records)
    artifact = next((r for r in records if r["id"] == build and r["type"] == "artifact"), None)
    if artifact is None:
        raise PassportError(f"the build artifact {build} is not among the records")
    if artifact["kind"] != "build":
        raise PassportError(f"{build} is an artifact of kind {artifact['kind']}, not build")

    if approve_release_by:
        at = _timestamp(issued_at)  # the release is approved when the passport is issued
        records.append(
            {
                "ois": "0.1",
                "type": "decision",
                "id": release_decision_id or _release_decision_id(build),
                "createdAt": at,
                "gate": "release",
                "subject": build,
                "subjectDigest": copy.deepcopy(artifact["digest"]),
                "outcome": release_outcome,
                "decidedBy": approve_release_by,
                "decidedAt": at,
            }
        )

    listed = intents or latest_approved(records)
    if not listed:
        raise PassportError("no approved intent among the records; name the intents to list")
    predicate: Record = {
        "ois": "0.1",
        "release": {"name": release.name, "version": release.version}
        | ({"location": release.location} if release.location else {}),
        "intents": listed,
    }
    if scope:
        predicate["scope"] = scope
    predicate["records"] = records
    if assess_coverage:
        measured = coverage(
            records, subject=build, intents=listed, scope=scope, signers=signers
        ).measurements()
        if measured:
            predicate["assessments"] = measured
    predicate |= {"issuedBy": issued_by, "issuedAt": _timestamp(issued_at)}
    statement: Record = {
        "_type": STATEMENT_TYPE,
        "subject": [
            {"name": subject_name or release.name, "digest": copy.deepcopy(artifact["digest"])}
        ],
        "predicateType": PASSPORT_PREDICATE_TYPE,
        "predicate": predicate,
    }
    violations = [r for r in validate(statement).reports if r.level == "violation"]
    if violations:
        details = "; ".join(f"[{r.rule}] {r.path}: {r.message}" for r in violations)
        raise PassportError(f"the passport would not conform: {details}")
    return statement


def _records(document: object) -> list[Record]:
    if is_envelope(document):  # a signed bundle or passport: its records, not its signatures
        assert isinstance(document, dict)
        document = open_envelope(document, []).payload
    if not isinstance(document, dict):
        raise PassportError("expected a JSON object: a record, a bundle or a passport")
    if document.get("_type") == STATEMENT_TYPE:
        found: list[Record] = document["predicate"]["records"]
        return found
    if document.get("type") == "bundle":
        records: list[Record] = document["records"]
        return records
    return [document]


def latest_approved(records: list[Record]) -> list[str]:
    """Per intent id, in order of appearance, its latest approved version (R15)."""
    latest: dict[str, int] = {}
    for record in records:
        if record["type"] == "intent" and record.get("status") == "approved":
            latest[record["id"]] = max(latest.get(record["id"], 0), record["version"])
    return [f"{intent_id}@{version}" for intent_id, version in latest.items()]


def _release_decision_id(build: str) -> str:
    if "/artifacts/" not in build:
        raise PassportError("cannot derive a decision id from the build id; pass one explicitly")
    return f"{build.replace('/artifacts/', '/decisions/', 1)}-release"


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
