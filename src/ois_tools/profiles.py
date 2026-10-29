"""Profiles this validator knows (section 10.2). A profile applies when a record lists it or the
verifier asks for it; its rules are reported as violations under the profile's rule IDs."""

from collections.abc import Iterable, Iterator

from ois_tools.document import Document
from ois_tools.report import Level, Report

REGULATED = "https://openintentschema.io/profiles/regulated/v0.1"
NAMES = {"regulated/v0.1": REGULATED}
BEFORE_SIGNING = frozenset({"regulated/P3"})  # rules a document can meet only once it is signed


class ProfileError(ValueError):
    """The profile is not known."""


def resolve(name: str) -> str:
    """The URI of a profile given by URI or short name, such as `regulated/v0.1`."""
    uri = NAMES.get(name, name)
    if uri not in NAMES.values():
        known = ", ".join(NAMES)
        raise ProfileError(f"unknown profile {name!r}; known: {known}")
    return uri


def check(document: Document, profiles: Iterable[str] = ()) -> Iterator[Report]:
    listed = {uri for entry in document.entries for uri in entry.record.get("profiles", [])}
    if REGULATED in {*profiles, *listed}:
        yield from _regulated(document)


def _regulated(document: Document) -> Iterator[Report]:
    for entry in document.of_type("intent"):
        if "risk" not in entry.record:
            yield _violation("P1", "an intent carries risk", entry.path)
    if document.container:
        for entry in document.of_type("decision"):
            decision = entry.record
            if decision["gate"] not in ("intent-approval", "release"):
                continue
            actor = document.record(decision["decidedBy"])
            if actor is not None and actor["type"] == "actor" and actor["kind"] != "human":
                kind = (
                    f"an {actor['kind']}" if actor["kind"][0] in "aeiou" else f"a {actor['kind']}"
                )
                message = f"a {decision['gate']} decision is made by a human, not {kind}"
                yield _violation("P2", message, f"{entry.path}.decidedBy")
        for entry in document.of_type("evidence"):
            for index, reference in enumerate(entry.record.get("inputs", [])):
                dataset = document.record(reference)
                if (
                    dataset is not None
                    and dataset["type"] == "artifact"
                    and dataset["kind"] == "dataset"
                    and dataset.get("classification") == "production"
                ):
                    message = f"no production dataset as test input: {reference}"
                    yield _violation("P4", message, f"{entry.path}.inputs[{index}]")
    if document.passport is not None and not document.enveloped:
        yield _violation("P3", "a passport is signed (DSSE envelope or Sigstore bundle)", "$")


def _violation(rule: str, message: str, path: str) -> Report:
    return Report(Level.VIOLATION, f"regulated/{rule}", message, path)
