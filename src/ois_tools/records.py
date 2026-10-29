"""Write artifact, actor, evidence, decision and finding records and bundles (section 4), each
checked against Appendix B and R17. Content-derived ids keep recording idempotent."""

import copy
import hashlib
import json
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import rfc8785

from ois_tools import digests
from ois_tools.passports import PassportError, collect
from ois_tools.validator import validate

Record = dict[str, Any]

# gate: subject type, subject kinds (empty: any), outcomes (section 4.6, well-known gates)
GATES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "intent-approval": ("intent", (), ("approved", "rejected")),
    "design-approval": ("artifact", ("contract", "document"), ("approved", "rejected")),
    "merge": ("artifact", ("code",), ("passed", "blocked", "waived")),
    "release": ("artifact", ("build",), ("approved", "rejected", "passed", "blocked", "waived")),
    "evidence-revocation": ("evidence", (), ("rejected",)),
    "finding-resolution": ("finding", (), ("approved", "rejected")),
}
DIGEST_REQUIRED = ("intent-approval", "release")

COLLECTIONS = {
    "intent": "intents",
    "artifact": "artifacts",
    "actor": "actors",
    "evidence": "evidence",
    "decision": "decisions",
    "finding": "findings",
}


KEY_PATTERN = r"([A-Z][A-Z0-9]*-[0-9]+)"  # a criterion key such as AC-1; group 1 is the key


class RecordError(ValueError):
    """The record cannot be written as asked."""


@dataclass
class Recorded:
    """What an adapter recorded from a test run."""

    records: list[Record]
    adapter: str  # reference of the adapter actor
    unmatched: list[str] = field(default_factory=list)  # results without evidence, and why
    notes: list[str] = field(default_factory=list)  # evidence not derived from the intent, why
    without_key: int = 0  # tests that name no criterion


@dataclass(frozen=True)
class Source:
    """What an artifact record points at."""

    location: str
    digest: dict[str, str]
    name: str  # readable part of a derived id
    path: str | None = None


def file_source(path: Path, location: str | None = None) -> Source:
    """A file by its sha256. Without a location the file must be committed in Git, unchanged; it
    is then located at the repository's HEAD."""
    digest = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if location is not None:
        return Source(location, digest, path.name)
    root = _git_root(path.parent)
    if root is None:
        raise RecordError(f"{path} is not in a Git working tree; pass --location")
    relative = path.resolve().relative_to(root).as_posix()
    if _run_git(root, "ls-files", "--error-unmatch", "--", relative).returncode != 0:
        raise RecordError(f"{relative} is not committed in Git; commit it or pass --location")
    if _run_git(root, "diff", "--quiet", "HEAD", "--", relative).returncode != 0:
        raise RecordError(f"{relative} has uncommitted changes; commit them or pass --location")
    commit = _git(root, "rev-parse", "HEAD")
    return Source(_location(root), digest | {"gitCommit": commit}, path.name, relative)


def commit_source(repo: Path) -> Source:
    """The repository at its HEAD commit (section 6.4 allows gitCommit alone in Git). It is named
    after its origin, since a checkout's folder name depends on where it was cloned."""
    root = _git_root(repo)
    if root is None:
        raise RecordError(f"{repo} is not in a Git working tree")
    commit = _git(root, "rev-parse", "HEAD")
    location = _location(root)  # git+<origin URL>@<commit>
    origin = location.removeprefix("git+").rpartition("@")[0]
    name = origin.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or root.name
    return Source(location, {"gitCommit": commit}, name)


def digest_source(digest: str, *, location: str, name: str | None = None) -> Source:
    """An artifact whose digest is already known, such as a container image in a registry."""
    tail = re.sub(r"[?#].*$", "", location).rstrip("/")
    return Source(location, parse_digest(digest), name or re.split(r"[/:]", tail)[-1])


def parse_digest(text: str) -> dict[str, str]:
    match = re.fullmatch(r"([A-Za-z0-9]+):([0-9a-f]+)", text)
    if match is None:
        raise RecordError(f"{text!r}: expected ALGORITHM:HEX, for example sha256:4f3c…")
    return {match[1]: match[2]}


def artifact(
    source: Source,
    *,
    kind: str,
    produced_by: str,
    created_at: datetime,
    id: str | None = None,
    namespace: str | None = None,
    inputs: Iterable[str] = (),
    context: Iterable[str] = (),
    context_recorded_by: str | None = None,
    classification: str | None = None,
    media_type: str | None = None,
) -> Record:
    anchor = next(iter(source.digest.values()))
    fields: Record = {"kind": kind, "location": source.location}
    if source.path is not None:
        fields["path"] = source.path
    fields |= {"digest": dict(source.digest), "producedBy": produced_by}
    fields |= _present(
        inputs=list(inputs),
        context=list(context),
        contextRecordedBy=context_recorded_by,
        classification=classification,
        mediaType=media_type,
    )
    name = id or _id(namespace, "artifacts", f"{_slug(source.name)}-{anchor[:12]}")
    return _checked(_record("artifact", name, created_at, fields))


def actor(
    *,
    kind: str,
    name: str,
    created_at: datetime,
    id: str | None = None,
    namespace: str | None = None,
    version: str | None = None,
    models: Iterable[Record] = (),
    identity: Record | None = None,
    operator: str | None = None,
    autonomy_level: int | None = None,
) -> Record:
    """Each actor version is its own record (section 4.4); a changed agent is a new version."""
    subject = (identity or {}).get("subject", "")
    if kind == "human" and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", subject):
        raise RecordError(
            "a human's identity subject must not be an e-mail address (specification section "
            "12); use a pseudonymous subject or leave the identity out"
        )
    listed = list(models)
    fields: Record = {"kind": kind, "name": name}
    fields |= _present(
        version=version,
        model=listed[0] if len(listed) == 1 else listed,
        identity=identity,
        operator=operator,
        autonomyLevel=autonomy_level,
    )
    name_id = id or _id(namespace, "actors", _slug(name))
    return _checked(_record("actor", name_id, created_at, fields))


def model(text: str) -> Record:
    """`PROVIDER:NAME:VERSION`; the name may contain colons, as in `local:llama3:8b:q4`."""
    provider, _, rest = text.partition(":")
    name, _, version = rest.rpartition(":")
    if not (provider and name and version):
        raise RecordError(f"{text!r}: expected PROVIDER:NAME:VERSION, for example acme-ai:coder:3")
    return {"provider": provider, "name": name, "version": version}


def evidence(
    *,
    criterion: str,
    test: str,
    derived_from: str,
    result: str,
    recorded_by: str,
    observed_at: datetime,
    created_at: datetime,
    id: str | None = None,
    namespace: str | None = None,
    subject: str | None = None,
    inputs: Iterable[str] = (),
    measures: Iterable[Record] = (),
    run: str | None = None,
) -> Record:
    fields: Record = {"criterion": criterion, "test": test}
    fields |= _present(subject=subject, inputs=list(inputs))
    fields |= {
        "derivedFrom": derived_from,
        "result": result,
        "observedAt": _timestamp(observed_at),
        "recordedBy": recorded_by,
    }
    fields |= _present(measures=list(measures), run=run)
    key = criterion.rpartition("#")[2]
    name = id or _id(namespace, "evidence", f"{key}-{_content_hash(fields)}")
    return _checked(_record("evidence", name, created_at, fields))


def measurement(text: str) -> Record:
    """`NAME=VALUE`: `responseTimeP95=420ms`, `coverage=87.5%`, `flaky=false`, `grade=B`."""
    match = re.fullmatch(r"([^=\s][^=]*)=(.+)", text)
    if match is None:
        raise RecordError(f"{text!r}: expected NAME=VALUE, for example responseTimeP95=420ms")
    name, value = match[1].strip(), match[2].strip()
    number = re.fullmatch(r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([^\d\s.+-]\S*)?", value)
    if number is not None:
        text_value = number[1]
        parsed = float(text_value) if any(c in text_value for c in ".eE") else int(text_value)
        return {"name": name, "value": parsed} | ({"unit": number[2]} if number[2] else {})
    if value in ("true", "false"):
        return {"name": name, "value": value == "true"}
    return {"name": name, "value": value}


def decision(
    *,
    gate: str,
    subject: str,
    decided_by: str,
    decided_at: datetime,
    records: list[Record],
    outcome: str | None = None,
    subject_digest: str | None = None,
    id: str | None = None,
    namespace: str | None = None,
    evidence: Iterable[str] = (),
    assessments: Iterable[Record] = (),
    rationale: str | None = None,
    policy: str | None = None,
    policy_version: str | None = None,
) -> Record:
    """A gate outcome. Its subject digest is taken from the subject among `records` unless given;
    for an intent approval `ois intent approve` also seals the intent."""
    known = GATES.get(gate)
    outcomes = known[2] if known else ()
    if outcome is None:
        if len(outcomes) != 1:
            choices = f": {', '.join(outcomes)}" if outcomes else ""
            raise RecordError(f"pass the outcome of the {gate} decision{choices}")
        outcome = outcomes[0]
    if outcomes and outcome not in outcomes:
        raise RecordError(
            f"the outcome of a {gate} decision is one of {', '.join(outcomes)}, not {outcome!r}"
        )
    if gate == "evidence-revocation" and not rationale:
        raise RecordError("an evidence revocation states its rationale (R11)")

    target = _find(records, subject)
    if target is not None and known is not None:
        _check_subject(gate, known, target)
    if target is not None and gate == "finding-resolution" and outcome == "approved":
        _check_resolution(target, decided_by, records)
    elif target is not None and gate == "finding-resolution":
        _check_current(target, records)

    digest = parse_digest(subject_digest) if subject_digest else None
    held = target.get("digest") if target is not None else None
    if digest is not None and held is not None and not digests.equal(digest, held):
        raise RecordError(f"--subject-digest differs from the digest of {subject}")
    digest = digest or (copy.deepcopy(held) if held else None)
    if digest is None and gate in DIGEST_REQUIRED:
        raise RecordError(
            f"a {gate} decision binds the digest of its subject: add the records that hold "
            f"{subject} (--records) or pass --subject-digest"
        )

    at = _timestamp(decided_at)
    fields: Record = {"gate": gate, "subject": subject}
    fields |= _present(subjectDigest=digest)
    fields |= {"outcome": outcome, "decidedBy": decided_by, "decidedAt": at}
    if policy:
        fields["policy"] = {"id": policy} | ({"version": policy_version} if policy_version else {})
    fields |= _present(evidence=list(evidence), assessments=list(assessments), rationale=rationale)
    name = id or _id(
        namespace, "decisions", f"{_tail(subject)}-{_slug(gate)}-{_content_hash(fields)}"
    )
    return _checked(_record("decision", name, decided_at, fields))


def finding(
    *,
    summary: str,
    severity: str,
    source: str,
    reported_by: str,
    observed_at: datetime,
    created_at: datetime,
    id: str | None = None,
    namespace: str | None = None,
    criterion: str | None = None,
    intent: str | None = None,
    subject: str | None = None,
    ticket: str | None = None,
    cause: str | None = None,
    resolved_by: Iterable[str] = (),
) -> Record:
    if not (criterion or intent or subject):
        raise RecordError("a finding names the criterion, intent or subject it concerns")
    fields = _present(criterion=criterion, intent=intent, subject=subject)
    fields |= {
        "source": source,
        "severity": severity,
        "summary": summary,
        "observedAt": _timestamp(observed_at),
        "reportedBy": reported_by,
    }
    fields |= _present(ticket=ticket, cause=cause, resolvedBy=list(resolved_by))
    name = id or _id(namespace, "findings", f"F-{_content_hash(fields)}")
    return _checked(_record("finding", name, created_at, fields))


def supersede(
    previous: Record,
    *,
    records: list[Record],
    created_at: datetime,
    severity: str | None = None,
    summary: str | None = None,
    ticket: str | None = None,
    cause: str | None = None,
    resolved_by: Iterable[str] = (),
) -> Record:
    """The next record of a finding chain (section 4.7): FND-18 → FND-18-r2 → FND-18-r3."""
    if previous.get("type") != "finding":
        raise RecordError(f"only findings are superseded this way, not a {previous.get('type')!r}")
    _check_current(previous, records)
    fields = {
        k: copy.deepcopy(v)
        for k, v in previous.items()
        if k not in ("ois", "type", "id", "createdAt", "supersedes")
    }
    fields |= _present(severity=severity, summary=summary, ticket=ticket, cause=cause)
    resolutions = [*fields.get("resolvedBy", []), *resolved_by]
    fields |= _present(resolvedBy=list(dict.fromkeys(resolutions)))
    fields["supersedes"] = previous["id"]
    revision = re.fullmatch(r"(.*)-r([0-9]+)", previous["id"])
    base, number = (revision[1], int(revision[2]) + 1) if revision else (previous["id"], 2)
    return _checked(_record("finding", f"{base}-r{number}", created_at, fields))


def bundle(
    records: Iterable[object],
    *,
    created_at: datetime,
    id: str | None = None,
    namespace: str | None = None,
) -> Record:
    """A bundle of the records, each once. It must be self-contained (R10)."""
    try:
        members = collect(records)
    except PassportError as error:
        raise RecordError(str(error)) from error
    anchor = hashlib.sha256(rfc8785.dumps(members)).hexdigest()[:16]
    made = _record("bundle", id or _id(namespace, "bundles", anchor), created_at, {})
    made["records"] = members
    violations = [r for r in validate(made).reports if r.level == "violation"]
    if violations:
        details = "; ".join(f"[{r.rule}] {r.path}: {r.message}" for r in violations)
        raise RecordError(f"the bundle would not conform: {details}")
    return made


def reference(record: Record) -> str:
    """How other records reference this one: `<id>@<version>` for intents and actors."""
    if record["type"] in ("intent", "actor") and "version" in record:
        return f"{record['id']}@{record['version']}"
    return str(record["id"])


def file_name(record: Record) -> str:
    """`actors/ci-build-v7.ois.json`, `artifacts/invoice-py-4f3c2b1a9e0d.ois.json`."""
    stem = record["id"].rstrip("/").rsplit("/", 1)[-1]
    if "version" in record:
        stem = f"{stem}-v{record['version']}"
    return f"{COLLECTIONS[record['type']]}/{stem}.ois.json"


def store(record: Record, output_dir: Path) -> tuple[Path, bool]:
    """Write the record to `output_dir` unless it is already there (False); a different record
    under its id is refused, since records never change (section 6.3)."""
    path = output_dir / file_name(record)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or not same(existing, record):
            advice = (
                "record a new version"
                if record["type"] == "actor"
                else "a different record needs a different id (R10)"
            )
            raise RecordError(
                f"{path} already holds {reference(record)} with other content; "
                f"records never change: {advice}"
            )
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path, True


def same(a: Record, b: Record) -> bool:
    """The same record, written at another time."""
    return {k: v for k, v in a.items() if k != "createdAt"} == {
        k: v for k, v in b.items() if k != "createdAt"
    }


def _check_subject(
    gate: str, known: tuple[str, tuple[str, ...], tuple[str, ...]], target: Record
) -> None:
    kind, kinds, _ = known
    if target["type"] == kind and (not kinds or target.get("kind") in kinds):
        return
    expected = _a(kind) + (f" of kind {' or '.join(kinds)}" if kinds else "")
    actual = _a(target["type"]) + (f" of kind {target['kind']}" if "kind" in target else "")
    raise RecordError(f"the subject of a {gate} decision is {expected}, not {actual}")


def _check_current(finding: Record, records: list[Record]) -> None:
    for record in records:
        if record.get("type") == "finding" and record.get("supersedes") == finding["id"]:
            raise RecordError(
                f"R14: {finding['id']} is already superseded by {record['id']}; a finding has "
                "at most one successor, so continue from the current finding"
            )


def _check_resolution(finding: Record, decided_by: str, records: list[Record]) -> None:
    _check_current(finding, records)
    if not finding.get("resolvedBy"):
        raise RecordError(
            f"{finding['id']} lists no resolution in resolvedBy; record it first with "
            "ois record supersede --resolved-by"
        )
    involved = set()
    for resolution in finding["resolvedBy"]:
        record = _find(records, resolution) or {}
        for name in ("producedBy", "recordedBy", "decidedBy"):
            if name in record:
                involved.add(record[name].partition("@")[0])
    if decided_by.partition("@")[0] in involved:
        raise RecordError(
            f"R14: {decided_by} took part in the resolution of {finding['id']}; another actor "
            "confirms it"
        )


def _find(records: list[Record], reference: str) -> Record | None:
    """The record a reference names, resolved as validators do (section 6.2)."""
    base, _, rest = reference.partition("@")
    version = rest.partition("#")[0]
    for record in records:
        if record.get("id") != base:
            continue
        if (str(record.get("version")) == version) if version else ("version" not in record):
            return record
    return None


def _a(noun: str) -> str:
    return f"an {noun}" if noun[0] in "aeiou" else f"a {noun}"


def _record(type_: str, id_: str, created_at: datetime, fields: Record) -> Record:
    return {"ois": "0.1", "type": type_, "id": id_, "createdAt": _timestamp(created_at), **fields}


def _checked(record: Record) -> Record:
    problems = [r for r in validate(record).reports if r.level == "violation"]
    if problems:
        details = "; ".join(f"{r.path}: {r.message}" for r in problems)
        raise RecordError(f"the {record['type']} would not conform: {details}")
    return record


def _present(**fields: Any) -> Record:
    """The fields that carry a value; 0 and false do, None and empty lists do not."""
    return {k: v for k, v in fields.items() if v is not None and v != []}


def _id(namespace: str | None, collection: str, name: str) -> str:
    if not namespace:
        raise RecordError(
            "pass --id, or a namespace for derived ids with --namespace or OIS_NAMESPACE, "
            "for example https://ois.acme.example"
        )
    return f"{namespace.rstrip('/')}/{collection}/{name}"


def _content_hash(fields: Record) -> str:
    return hashlib.sha256(rfc8785.dumps(fields)).hexdigest()[:12]


def _tail(reference: str) -> str:
    """`INT-7-v2` for https://…/intents/INT-7@2#AC-1."""
    base, _, version = reference.partition("#")[0].partition("@")
    tail = base.rstrip("/").rsplit("/", 1)[-1]
    return f"{tail}-v{version}" if version else tail


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "record"


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def location_of(repo: Path) -> str:
    """git+<origin URL>@<HEAD commit>, without credentials; scp-like remotes become ssh URIs."""
    remote = _git(repo, "remote", "get-url", "origin")
    commit = _git(repo, "rev-parse", "HEAD")
    if "://" not in remote and (scp := re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", remote)):
        remote = f"ssh://{scp[1]}/{scp[2]}"
    parts = urlsplit(remote)
    if parts.scheme not in ("https", "http", "ssh", "git") or not parts.hostname:
        raise ValueError(f"cannot turn remote {remote!r} into a location; pass --location")
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return f"git+{urlunsplit((parts.scheme, host, parts.path, '', ''))}@{commit}"


def _location(root: Path) -> str:
    try:
        return location_of(root)
    except ValueError as error:
        raise RecordError(str(error)) from error


def _git_root(directory: Path) -> Path | None:
    try:
        found = _run_git(directory, "rev-parse", "--show-toplevel")
    except FileNotFoundError:  # no git installed
        return None
    return Path(found.stdout.strip()).resolve() if found.returncode == 0 else None


def _git(root: Path, *args: str) -> str:
    done = _run_git(root, *args)
    if done.returncode != 0:
        raise RecordError(f"git {' '.join(args)} failed in {root}: {done.stderr.strip()}")
    return done.stdout.strip()


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
