"""Command line interface: ois junit REPORT... --namespace URI --suite NAME, or with --intent REF
--test-producer ACTOR the evidence for an approved intent, written to <output-dir>."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError

from ois_tools import records
from ois_tools.authoring import AuthoringError, files, load
from ois_tools.formats import JUnitReport
from ois_tools.junit import Options, record_run, to_bundle
from ois_tools.passports import PassportError, collect
from ois_tools.records import KEY_PATTERN, RecordError
from ois_tools.validator import validate

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ois junit",
        description="Turn JUnit XML reports into an OIS bundle (Level 1, spec section 11.4), "
        "or with --intent into evidence for the criteria that tests name by key.",
    )
    parser.add_argument("reports", nargs="+", metavar="REPORT", help="JUnit XML file, or -")
    parser.add_argument(
        "--namespace",
        default=os.environ.get("OIS_NAMESPACE"),
        help="URI under your domain for record ids, e.g. https://ois.acme.example "
        "(default: $OIS_NAMESPACE)",
    )
    parser.add_argument("--suite", help="team or suite that owns the tests (without --intent)")
    parser.add_argument("--repo", type=Path, default=Path("."), help="Git working tree (default .)")
    parser.add_argument("--location", help="artifact location (default git+<origin>@<HEAD>)")
    parser.add_argument("--run", help="URI of the pipeline run")
    parser.add_argument("--output", type=Path, help="write the bundle here instead of stdout")
    intent = parser.add_argument_group("evidence for an approved intent")
    intent.add_argument("--intent", metavar="REF", help="<intent>@<version>")
    intent.add_argument("--test-producer", metavar="ACTOR", help="who wrote the tests")
    intent.add_argument("--subject", metavar="REF", help="the code or build under test")
    intent.add_argument(
        "--written-from-intent",
        action="store_true",
        help="the tests were written from the criteria they name, without the code",
    )
    intent.add_argument("--key-pattern", default=KEY_PATTERN, help="regex of a criterion key")
    intent.add_argument("--records", action="append", default=[], type=Path, metavar="PATH")
    intent.add_argument("--at", help="createdAt, RFC 3339 (default now)")
    intent.add_argument("--output-dir", type=Path, default=Path(".ois"))
    args = parser.parse_args(argv)

    if not args.namespace:
        parser.error("pass --namespace or set OIS_NAMESPACE")
    if args.intent is None:
        if args.suite is None:
            parser.error("pass --suite, or --intent for evidence of an approved intent")
        return _bundle(args)
    if args.test_producer is None:
        parser.error("--intent needs --test-producer")
    if args.output is not None:
        parser.error("--output writes a bundle; with --intent records go to --output-dir")
    return _evidence(args)


def _bundle(args: argparse.Namespace) -> int:
    try:
        reports = [_read(name) for name in args.reports]
        options = Options(
            namespace=args.namespace,
            suite=args.suite,
            repo=args.repo,
            location=args.location,
            run=args.run,
        )
        bundle = to_bundle(reports, options)
    except (OSError, ParseError, ValueError) as error:
        print(f"ois junit: {error}", file=sys.stderr)
        return EXIT_USAGE

    result = validate(bundle)
    if not result.conforms:
        for report in result.reports:
            print(
                f"ois junit: {report.level} [{report.rule}] {report.path}: {report.message}",
                file=sys.stderr,
            )
        return EXIT_INVALID

    text = json.dumps(bundle, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return EXIT_OK


def _evidence(args: argparse.Namespace) -> int:
    try:
        reports = [_read(name) for name in args.reports]
        known = [args.output_dir] if args.output_dir.is_dir() else []
        found = collect(load(path) for path in files([*known, *args.records]))
        recorded = record_run(
            reports,
            intent=_intent(found, args.intent),
            test_producer=args.test_producer,
            namespace=args.namespace,
            repo=args.repo,
            created_at=_moment(args.at),
            known=found,
            subject=args.subject,
            run=args.run,
            location=args.location,
            key_pattern=args.key_pattern,
            written_from_intent=args.written_from_intent,
        )
        for record in recorded.records:
            path, written = records.store(record, args.output_dir)
            print(f"ois junit: {'wrote' if written else 'unchanged'} {path}", file=sys.stderr)
    except RecordError as error:
        print(f"ois junit: {error}", file=sys.stderr)
        return EXIT_INVALID
    except (OSError, ParseError, AuthoringError, PassportError, ValueError) as error:
        print(f"ois junit: {error}", file=sys.stderr)
        return EXIT_USAGE

    evidence = [r for r in recorded.records if r["type"] == "evidence"]
    derived = sum(1 for e in evidence if e["derivedFrom"] == "intent")
    print(
        f"ois junit: {len(evidence)} results: {derived} derived from the intent, "
        f"{len(evidence) - derived} not",
        file=sys.stderr,
    )
    if recorded.without_key:
        tests = "test" if recorded.without_key == 1 else "tests"
        print(f"ois junit: {recorded.without_key} {tests} without a criterion key", file=sys.stderr)
    for item in [*recorded.notes, *(f"no evidence: {u}" for u in recorded.unmatched)]:
        print(f"ois junit: {item}", file=sys.stderr)
    return EXIT_OK


def _read(name: str) -> JUnitReport:
    if name == "-":
        return JUnitReport(sys.stdin.buffer.read())
    path = Path(name)
    written_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return JUnitReport(path.read_bytes(), written_at)


def _intent(found: list[dict[str, Any]], reference: str) -> dict[str, Any]:
    base, _, version = reference.partition("@")
    for record in found:
        if (
            record["type"] == "intent"
            and record["id"] == base
            and str(record["version"]) == version
        ):
            return record
    raise RecordError(f"{reference} is not among the records (--records, --output-dir)")


def _moment(text: str | None) -> datetime:
    if text is None:
        return datetime.now(UTC)
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError(f"{text} has no time zone offset")
    return moment


if __name__ == "__main__":
    sys.exit(main())
