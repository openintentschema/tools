"""Command line interface: ois-record artifact | actor | evidence | decision | finding | supersede |
bundle (see --help)

Each command writes one record to <output-dir>/<type>s/ and prints the reference other records use
for it, so that a pipeline can pass it on: BUILD=$(ois-record artifact dist/app.whl ...).
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from ois_validator import records
from ois_validator.authoring import AuthoringError, files, load
from ois_validator.passports import PassportError, collect
from ois_validator.records import RecordError
from ois_validator.validator import validate

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

ARTIFACT_KINDS = ("code", "test", "contract", "dataset", "document", "build", "other")
SEVERITIES = ("low", "medium", "high", "critical")
CAUSES = ("spec-gap", "implementation", "test-gap", "gate-gap", "review", "unknown")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        at = _moment(args.at)
        if args.command == "bundle":
            made = records.bundle(
                [load(path) for path in files(args.inputs)],
                created_at=at,
                id=args.id,
                namespace=args.namespace,
            )
            for report in validate(made).reports:
                print(f"ois-record: {report.level} [{report.rule}] {report.path}: {report.message}",
                      file=sys.stderr)  # fmt: skip
            text = json.dumps(made, indent=2, ensure_ascii=False) + "\n"
            if args.output:
                args.output.write_text(text, encoding="utf-8")
            else:
                sys.stdout.write(text)
            return EXIT_OK
        record = COMMANDS[args.command](args, at)
        print(_store(record, args.output_dir))
    except RecordError as error:
        print(f"ois-record: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except (OSError, AuthoringError, PassportError, ValueError) as error:
        print(f"ois-record: {error}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def _artifact(args: argparse.Namespace, at: datetime) -> dict[str, Any]:
    if args.file is not None:
        source = records.file_source(args.file, args.location)
    elif args.commit is not None:
        source = records.commit_source(args.commit)
    elif args.location is None:
        raise RecordError("--digest needs --location: where the artifact lives")
    else:
        source = records.digest_source(args.digest, location=args.location)
    if args.name:
        source = records.Source(source.location, source.digest, args.name, source.path)
    return records.artifact(
        source,
        kind=args.kind,
        produced_by=args.produced_by,
        created_at=at,
        id=args.id,
        namespace=args.namespace,
        inputs=args.input,
        context=args.context,
        context_recorded_by=args.context_recorded_by,
        classification=args.classification,
        media_type=args.media_type,
    )


def _actor(args: argparse.Namespace, at: datetime) -> dict[str, Any]:
    identity: dict[str, str] = {}
    if args.identity_issuer or args.identity_subject:
        identity = {"issuer": args.identity_issuer, "subject": args.identity_subject}
    if args.key_id:
        identity["keyId"] = args.key_id
    return records.actor(
        kind=args.kind,
        name=args.name,
        created_at=at,
        id=args.id,
        namespace=args.namespace,
        version=args.version,
        models=[records.model(text) for text in args.model],
        identity={k: v for k, v in identity.items() if v} or None,
        operator=args.operator,
        autonomy_level=args.autonomy_level,
    )


def _evidence(args: argparse.Namespace, at: datetime) -> dict[str, Any]:
    return records.evidence(
        criterion=args.criterion,
        test=args.test,
        derived_from=args.derived_from,
        result=args.result,
        recorded_by=args.recorded_by,
        observed_at=_moment(args.observed_at) if args.observed_at else at,
        created_at=at,
        id=args.id,
        namespace=args.namespace,
        subject=args.subject,
        inputs=args.input,
        measures=[records.measurement(text) for text in args.measure],
        run=args.run,
    )


def _decision(args: argparse.Namespace, at: datetime) -> dict[str, Any]:
    return records.decision(
        gate=args.gate,
        subject=args.subject,
        decided_by=args.by,
        decided_at=at,
        records=_known(args),
        outcome=args.outcome,
        subject_digest=args.subject_digest,
        id=args.id,
        namespace=args.namespace,
        evidence=args.evidence,
        assessments=[records.measurement(text) for text in args.assessment],
        rationale=args.rationale,
        policy=args.policy,
        policy_version=args.policy_version,
    )


def _finding(args: argparse.Namespace, at: datetime) -> dict[str, Any]:
    return records.finding(
        summary=args.summary,
        severity=args.severity,
        source=args.source,
        reported_by=args.reported_by,
        observed_at=_moment(args.observed_at) if args.observed_at else at,
        created_at=at,
        id=args.id,
        namespace=args.namespace,
        criterion=args.criterion,
        intent=args.intent,
        subject=args.subject,
        ticket=args.ticket,
        cause=args.cause,
        resolved_by=args.resolved_by,
    )


def _supersede(args: argparse.Namespace, at: datetime) -> dict[str, Any]:
    previous = load(args.file)
    if not isinstance(previous, dict):
        raise RecordError(f"{args.file} holds no record")
    return records.supersede(
        previous,
        records=_known(args),
        created_at=at,
        severity=args.severity,
        summary=args.summary,
        ticket=args.ticket,
        cause=args.cause,
        resolved_by=args.resolved_by,
    )


COMMANDS = {
    "artifact": _artifact,
    "actor": _actor,
    "evidence": _evidence,
    "decision": _decision,
    "finding": _finding,
    "supersede": _supersede,
}


def _store(record: dict[str, Any], output_dir: Path) -> str:
    """Write the record unless it is already there; a record never changes (section 6.3)."""
    path = output_dir / records.file_name(record)
    if path.exists():
        existing = load(path)
        if not isinstance(existing, dict) or not records.same(existing, record):
            advice = (
                "record a new version"
                if record["type"] == "actor"
                else "a different record needs a different id (R10)"
            )
            raise RecordError(
                f"{path} already holds {records.reference(record)} with other content; "
                f"records never change: {advice}"
            )
        print(f"ois-record: unchanged {path}", file=sys.stderr)
        return records.reference(existing)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"ois-record: wrote {path}", file=sys.stderr)
    return records.reference(record)


def _known(args: argparse.Namespace) -> list[dict[str, Any]]:
    """The records already written to the output directory, and those named with --records."""
    paths = [args.output_dir] if args.output_dir.is_dir() else []
    return collect(load(path) for path in files([*paths, *args.records]))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ois-record",
        description="Write OIS records: artifacts with their digests, actors, evidence, "
        "decisions, findings and bundles. Each command prints the reference of its record.",
    )
    parser.add_argument("--version", action="version", version=version("ois-validator"))
    commands = parser.add_subparsers(dest="command", required=True)

    art = _command(commands, "artifact", "an artifact with the digest of a file, commit or image")
    source = art.add_mutually_exclusive_group(required=True)
    source.add_argument("file", nargs="?", type=Path, help="file; located in Git if committed")
    source.add_argument("--commit", type=Path, metavar="REPO", help="the repository at HEAD")
    source.add_argument("--digest", metavar="ALG:HEX", help="a known digest, with --location")
    art.add_argument("--kind", required=True, choices=ARTIFACT_KINDS)
    art.add_argument("--produced-by", required=True, metavar="ACTOR")
    art.add_argument("--location", help="URI of the artifact, e.g. a purl (default: Git)")
    art.add_argument("--name", help="readable part of the derived id (default: file name)")
    art.add_argument("--input", action="append", default=[], metavar="REF")
    art.add_argument("--context", action="append", default=[], metavar="REF")
    art.add_argument("--context-recorded-by", metavar="ACTOR")
    art.add_argument("--classification", choices=("synthetic", "anonymized", "production"))
    art.add_argument("--media-type")

    act = _command(commands, "actor", "a human, agent or tool")
    act.add_argument("--kind", required=True, choices=("human", "agent", "tool"))
    act.add_argument("--name", required=True, help="display name; a pseudonym for humans")
    act.add_argument("--version", help="required for agents and tools")
    act.add_argument(
        "--model", action="append", default=[], metavar="PROVIDER:NAME:VERSION",
        help="model of an agent; repeat for agents that route between models",
    )  # fmt: skip
    act.add_argument("--identity-issuer", metavar="URI")
    act.add_argument("--identity-subject", help="pseudonymous for humans, never an e-mail")
    act.add_argument("--key-id")
    act.add_argument("--operator", help="team or organization responsible")
    act.add_argument("--autonomy-level", type=int, choices=range(4))

    evi = _command(commands, "evidence", "one result of one test for one criterion")
    evi.add_argument("--criterion", required=True, metavar="REF", help="<intent>@<version>#<key>")
    evi.add_argument("--test", required=True, metavar="REF", help="the test artifact")
    evi.add_argument("--subject", metavar="REF", help="the code or build under test")
    evi.add_argument(
        "--derived-from", required=True, choices=("intent", "implementation", "unknown")
    )
    evi.add_argument("--result", required=True, choices=("pass", "fail", "skipped", "error"))
    evi.add_argument("--recorded-by", required=True, metavar="ACTOR")
    evi.add_argument("--observed-at", help="RFC 3339 (default --at)")
    evi.add_argument("--input", action="append", default=[], metavar="REF")
    evi.add_argument("--measure", action="append", default=[], metavar="NAME=VALUE")
    evi.add_argument("--run", metavar="URI", help="link to the pipeline or test run")

    dec = _command(commands, "decision", "a gate outcome bound to its subject's digest", known=True)
    dec.add_argument("--gate", required=True, help="e.g. merge, release, evidence-revocation")
    dec.add_argument("--subject", required=True, metavar="REF")
    dec.add_argument("--outcome", choices=("approved", "rejected", "passed", "blocked", "waived"))
    dec.add_argument("--by", required=True, metavar="ACTOR", help="the deciding actor")
    dec.add_argument("--subject-digest", metavar="ALG:HEX", help="default: from the subject")
    dec.add_argument("--evidence", action="append", default=[], metavar="REF")
    dec.add_argument("--assessment", action="append", default=[], metavar="NAME=VALUE")
    dec.add_argument("--rationale")
    dec.add_argument("--policy", metavar="URI")
    dec.add_argument("--policy-version")

    fnd = _command(commands, "finding", "a defect or incident")
    fnd.add_argument("--summary", required=True, help="no personal data")
    fnd.add_argument("--severity", required=True, choices=SEVERITIES)
    fnd.add_argument(
        "--source", required=True, choices=("production", "test", "review", "customer", "other")
    )
    fnd.add_argument("--reported-by", required=True, metavar="ACTOR")
    fnd.add_argument("--criterion", metavar="REF")
    fnd.add_argument("--intent", metavar="REF", help="<intent>@<version>")
    fnd.add_argument("--subject", metavar="REF")
    fnd.add_argument("--observed-at", help="RFC 3339 (default --at)")
    fnd.add_argument("--ticket", metavar="URI")
    fnd.add_argument("--cause", choices=CAUSES)
    fnd.add_argument("--resolved-by", action="append", default=[], metavar="REF")

    sup = commands.add_parser("supersede", help="classify or resolve a finding: its next record")
    sup.add_argument("file", type=Path, help="the current finding record")
    sup.add_argument("--severity", choices=SEVERITIES)
    sup.add_argument("--summary")
    sup.add_argument("--ticket", metavar="URI")
    sup.add_argument("--cause", choices=CAUSES)
    sup.add_argument("--resolved-by", action="append", default=[], metavar="REF")
    sup.add_argument("--at", help="createdAt, RFC 3339 (default now)")
    sup.add_argument("--output-dir", type=Path, default=Path(".ois"))
    _records_option(sup)

    bun = commands.add_parser("bundle", help="a self-contained bundle of records, checked")
    bun.add_argument("inputs", nargs="+", type=Path, metavar="INPUT", help="files or directories")
    bun.add_argument("--id", help="id of the bundle (default derived from its records)")
    bun.add_argument("--namespace", default=os.environ.get("OIS_NAMESPACE"))
    bun.add_argument("--at", help="createdAt, RFC 3339 (default now)")
    bun.add_argument("--output", type=Path, help="write here instead of stdout")
    return parser


def _command(
    commands: Any, name: str, help: str, *, known: bool = False
) -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = commands.add_parser(name, help=help)
    parser.add_argument("--id", help="id of the record (default derived)")
    parser.add_argument(
        "--namespace",
        default=os.environ.get("OIS_NAMESPACE"),
        help="URI for derived ids, e.g. https://ois.acme.example (default $OIS_NAMESPACE)",
    )
    parser.add_argument("--at", help="createdAt, RFC 3339 (default now)")
    parser.add_argument("--output-dir", type=Path, default=Path(".ois"))
    if known:
        _records_option(parser)
    return parser


def _records_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--records",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help="further records to look up references in (the output directory always counts)",
    )


def _moment(text: str | None) -> datetime:
    if text is None:
        return datetime.now(UTC)
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError(f"{text} has no time zone offset")
    return moment


if __name__ == "__main__":
    sys.exit(main())
