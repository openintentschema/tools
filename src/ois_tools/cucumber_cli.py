"""Command line interface: ois cucumber MESSAGES --intent REF --test-producer ACTOR

Writes the evidence of a Cucumber Messages run (NDJSON) for an approved intent to <output-dir>."""

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ois_tools import records
from ois_tools.authoring import AuthoringError, files, load
from ois_tools.cucumber import CucumberError, record_run
from ois_tools.passports import PassportError, collect
from ois_tools.records import KEY_PATTERN, RecordError

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ois cucumber",
        description="Record a Cucumber run (Cucumber Messages, NDJSON) as evidence for an intent.",
    )
    parser.add_argument("messages", help="NDJSON file of the message formatter, or -")
    parser.add_argument("--intent", required=True, metavar="REF", help="<intent>@<version>")
    parser.add_argument(
        "--test-producer", required=True, metavar="ACTOR", help="who wrote the features"
    )
    parser.add_argument("--subject", metavar="REF", help="the code or build under test")
    parser.add_argument("--namespace", default=os.environ.get("OIS_NAMESPACE"))
    parser.add_argument("--repo", type=Path, default=Path("."), help="Git checkout of the features")
    parser.add_argument("--location", help="location of the feature files (default: Git)")
    parser.add_argument("--run", metavar="URI", help="link to the pipeline run")
    parser.add_argument("--key-pattern", default=KEY_PATTERN, help="regex on a tag without @")
    parser.add_argument("--records", action="append", default=[], type=Path, metavar="PATH")
    parser.add_argument("--at", help="createdAt, RFC 3339 (default now)")
    parser.add_argument("--output-dir", type=Path, default=Path(".ois"))
    args = parser.parse_args(argv)

    try:
        lines = (
            sys.stdin.read() if args.messages == "-" else Path(args.messages).read_text("utf-8")
        ).splitlines()
        known = [args.output_dir] if args.output_dir.is_dir() else []
        found = collect(load(path) for path in files([*known, *args.records]))
        intent = _intent(found, args.intent)
        if not args.namespace:
            raise RecordError("pass --namespace or set OIS_NAMESPACE")
        recorded = record_run(
            lines,
            intent=intent,
            test_producer=args.test_producer,
            namespace=args.namespace,
            repo=args.repo,
            created_at=_moment(args.at),
            subject=args.subject,
            run=args.run,
            location=args.location,
            key_pattern=args.key_pattern,
        )
        for record in recorded.records:
            path, written = records.store(record, args.output_dir)
            print(f"ois cucumber: {'wrote' if written else 'unchanged'} {path}", file=sys.stderr)
    except (RecordError, CucumberError) as error:
        print(f"ois cucumber: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except (OSError, AuthoringError, PassportError, ValueError) as error:
        print(f"ois cucumber: {error}", file=sys.stderr)
        return EXIT_USAGE

    evidence = [r for r in recorded.records if r["type"] == "evidence"]
    derived = sum(1 for e in evidence if e["derivedFrom"] == "intent")
    print(
        f"ois cucumber: {len(evidence)} results: {derived} derived from the intent, "
        f"{len(evidence) - derived} not",
        file=sys.stderr,
    )
    for item in [*recorded.notes, *(f"no evidence: {u}" for u in recorded.unmatched)]:
        print(f"ois cucumber: {item}", file=sys.stderr)
    return EXIT_OK


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
