"""Author intents: digests, approval, the next version and what changed between two.

An approved intent never changes (specification section 6.3). Its digest covers everything but
`status` and `digest` (section 6.4), and its intent-approval decision binds that digest (R1). A new
version supersedes the previous one; evidence carries over only for criteria whose digest is
unchanged.
"""

import copy
from datetime import UTC, datetime
from typing import Any

from ois_validator import digests
from ois_validator.validator import validate

Record = dict[str, Any]


class IntentError(ValueError):
    """The intent cannot be sealed, approved or versioned as asked."""


def seal(intent: Record) -> Record:
    """A copy with every criterion digest and the intent digest computed."""
    sealed = _prepare(intent)
    for criterion in sealed["criteria"]:
        criterion["digest"] = {"sha256": digests.criterion_sha256(criterion)}
    sealed.pop("digest", None)
    sealed["digest"] = {"sha256": digests.intent_sha256(sealed)}
    _check(sealed)
    return sealed


def approve(
    intent: Record,
    *,
    decided_by: str,
    decided_at: datetime,
    decision_id: str | None = None,
    policy: str | None = None,
    policy_version: str | None = None,
) -> tuple[Record, Record]:
    """The approved intent and its intent-approval decision, bound by the intent's digest."""
    prepared = _prepare(intent)
    if prepared.get("status") == "withdrawn":
        raise IntentError("a withdrawn intent cannot be approved; create the next version")
    changed = (
        prepared.get("status") == "approved"
        and "digest" in prepared
        and seal(prepared)["digest"] != prepared["digest"]
    )
    if changed:
        raise IntentError(
            f"{_versioned(prepared)} was changed after approval: its digest no longer matches "
            "its content. Approved intents never change; create the next version instead."
        )
    approved = seal({**prepared, "status": "approved"})
    at = _timestamp(decided_at)
    decision: Record = {
        "ois": "0.1",
        "type": "decision",
        "id": decision_id or _approval_id(approved),
        "createdAt": at,
        "gate": "intent-approval",
        "subject": _versioned(approved),
        "subjectDigest": copy.deepcopy(approved["digest"]),
        "outcome": "approved",
        "decidedBy": decided_by,
        "decidedAt": at,
    }
    if policy:
        decision["policy"] = {
            "id": policy,
            **({"version": policy_version} if policy_version else {}),
        }
    _check(decision)
    return approved, decision


def next_version(intent: Record, *, created_at: datetime) -> Record:
    """A draft of the following version that supersedes this one, without digests."""
    current = _prepare(intent)
    following = copy.deepcopy(current)
    following["version"] = current["version"] + 1
    following["createdAt"] = _timestamp(created_at)
    following["status"] = "draft"
    following["supersedes"] = _versioned(current)
    following.pop("digest", None)
    for criterion in following["criteria"]:
        criterion.pop("digest", None)
    return following


def compare(old: Record, new: Record) -> list[tuple[str, str]]:
    """Per criterion key: unchanged (evidence carries over), changed, removed or added."""
    before = {c["key"]: digests.criterion_sha256(c) for c in _prepare(old)["criteria"]}
    after = {c["key"]: digests.criterion_sha256(c) for c in _prepare(new)["criteria"]}
    result = []
    for key, digest in before.items():
        if key not in after:
            result.append((key, "removed"))
        else:
            result.append((key, "unchanged" if after[key] == digest else "changed"))
    result.extend((key, "added") for key in after if key not in before)
    return result


def file_stem(intent: Record) -> str:
    """`INT-142-v3` for version 3 of https://…/intents/INT-142."""
    return f"{intent['id'].rstrip('/').rsplit('/', 1)[-1]}-v{intent['version']}"


def _prepare(intent: object) -> Record:
    if not isinstance(intent, dict):
        raise IntentError("an intent is a JSON object")
    if intent.get("type", "intent") != "intent":
        raise IntentError(f"expected an intent, not a {intent.get('type')!r} record")
    for name in ("id", "version", "criteria"):
        if name not in intent:
            raise IntentError(f"the intent has no {name!r}")
    rest = {k: v for k, v in copy.deepcopy(intent).items() if k not in ("ois", "type")}
    return {"ois": "0.1", "type": "intent", **rest}


def _check(record: Record) -> None:
    problems = [r for r in validate(record).reports if r.level == "violation"]
    if problems:
        details = "; ".join(f"{r.path}: {r.message}" for r in problems)
        raise IntentError(f"{record['type']} does not conform: {details}")


def _versioned(intent: Record) -> str:
    return f"{intent['id']}@{intent['version']}"


def _approval_id(intent: Record) -> str:
    if "/intents/" not in intent["id"]:
        raise IntentError("cannot derive a decision id from the intent id; pass one explicitly")
    return f"{intent['id'].replace('/intents/', '/decisions/', 1)}-v{intent['version']}-approval"


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
