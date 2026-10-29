"""Command line interface: ois measure RESULTS --intent REF --criterion KEY=METRIC:STAT --test FILE
--test-producer ACTOR

Writes evidence with measures from a k6 summary or JMeter results to <output-dir>."""

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ois_tools import records
from ois_tools.authoring import AuthoringError, files, load
from ois_tools.measures import record_run
from ois_tools.passports import PassportError, collect
from ois_tools.records import RecordError

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ois measure",
        description="Record load test results (k6 summary, JMeter JTL) as measured evidence "
        "for the non-functional criteria of an intent.",
    )
    parser.add_argument("results", help="k6 summary JSON or JMeter JTL CSV, or -")
    parser.add_argument("--intent", required=True, metavar="REF", help="<intent>@<version>")
    parser.add_argument(
        "--criterion",
        action="append",
        required=True,
        metavar="KEY=METRIC:STAT",
        help="e.g. AC-3=http_req_duration:p(95); repeatable",
    )
    parser.add_argument("--test", required=True, type=Path, help="the load test script or plan")
    parser.add_argument("--test-producer", required=True, metavar="ACTOR", help="who wrote it")
    parser.add_argument("--subject", metavar="REF", help="the code or build under test")
    parser.add_argument(
        "--written-from-intent",
        action="store_true",
        help="the test was written from the criteria it measures, without the code",
    )
    parser.add_argument("--namespace", default=os.environ.get("OIS_NAMESPACE"))
    parser.add_argument("--location", help="location of the test (default: Git)")
    parser.add_argument("--run", metavar="URI", help="link to the pipeline run")
    parser.add_argument("--records", action="append", default=[], type=Path, metavar="PATH")
    parser.add_argument("--at", help="createdAt, RFC 3339 (default now)")
    parser.add_argument("--output-dir", type=Path, default=Path(".ois"))
    args = parser.parse_args(argv)

    measures = {}
    for item in args.criterion:
        key, _, spec = item.partition("=")
        if not key or ":" not in spec:
            parser.error(f"--criterion {item}: expected KEY=METRIC:STAT")
        measures[key] = spec

    try:
        if args.results == "-":
            content, written_at = sys.stdin.buffer.read(), datetime.now(UTC)
        else:
            path = Path(args.results)
            content = path.read_bytes()
            written_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        known = [args.output_dir] if args.output_dir.is_dir() else []
        found = collect(load(path) for path in files([*known, *args.records]))
        if not args.namespace:
            raise RecordError("pass --namespace or set OIS_NAMESPACE")
        intent = _intent(found, args.intent)
        recorded = record_run(
            content,
            intent=intent,
            measures=measures,
            test=args.test,
            test_producer=args.test_producer,
            namespace=args.namespace,
            written_at=written_at,
            created_at=_moment(args.at),
            known=found,
            subject=args.subject,
            run=args.run,
            location=args.location,
            written_from_intent=args.written_from_intent,
        )
        for record in recorded.records:
            path, written = records.store(record, args.output_dir)
            print(f"ois measure: {'wrote' if written else 'unchanged'} {path}", file=sys.stderr)
    except RecordError as error:
        print(f"ois measure: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except (OSError, AuthoringError, PassportError, ValueError) as error:
        print(f"ois measure: {error}", file=sys.stderr)
        return EXIT_USAGE

    targets = {c["key"]: c.get("target", {}) for c in intent["criteria"]}
    for evidence in (r for r in recorded.records if r["type"] == "evidence"):
        key = evidence["criterion"].rpartition("#")[2]
        measure, target = evidence["measures"][0], targets[key]
        print(
            f"ois measure: {key}: {measure['name']} {measure['value']:.3g}{_unit(measure)}, "
            f"target {target['operator']} {target['value']:g}{_unit(target)}: "
            f"{evidence['result']}",
            file=sys.stderr,
        )
    for note in recorded.notes:
        print(f"ois measure: {note}", file=sys.stderr)
    return EXIT_OK


def _unit(item: dict[str, Any]) -> str:
    return f" {item['unit']}" if item.get("unit") else ""


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
