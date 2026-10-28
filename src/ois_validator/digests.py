"""Digests of criteria and intents (spec section 6.4), over their RFC 8785 canonical form."""

import hashlib
from typing import Any

import rfc8785

Digest = dict[str, str]  # in-toto DigestSet: algorithm -> lowercase hex

CRITERION_FIELDS = ("key", "statement", "notation", "kind", "target", "language")


def criterion_sha256(criterion: dict[str, Any]) -> str:
    """Over the criterion's own key, statement, notation, kind, target and language."""
    fields = {k: criterion[k] for k in CRITERION_FIELDS if k in criterion}
    return hashlib.sha256(rfc8785.dumps(fields)).hexdigest()


def intent_sha256(intent: dict[str, Any]) -> str:
    """Over the intent without its top-level digest and status, criterion digests included."""
    content = {k: v for k, v in intent.items() if k not in ("digest", "status")}
    return hashlib.sha256(rfc8785.dumps(content)).hexdigest()


def equal(a: Digest, b: Digest) -> bool:
    """Two digest sets name the same content: they share an algorithm and agree on each."""
    shared = a.keys() & b.keys()
    return bool(shared) and all(a[name] == b[name] for name in shared)
