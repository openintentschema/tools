"""Command line interface: ois-junit REPORT... --namespace URI --suite NAME"""

import argparse
import json
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from xml.etree.ElementTree import ParseError

from ois_validator.junit import JUnitReport, Options, to_bundle
from ois_validator.validator import validate

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ois-junit",
        description="Turn JUnit XML reports into an OIS bundle (Level 1, spec section 11.4).",
    )
    parser.add_argument("reports", nargs="+", metavar="REPORT", help="JUnit XML file, or -")
    parser.add_argument(
        "--namespace",
        required=True,
        help="URI under your domain for record ids, e.g. https://ois.acme.example",
    )
    parser.add_argument("--suite", required=True, help="team or suite that owns the tests")
    parser.add_argument("--repo", type=Path, default=Path("."), help="Git working tree (default .)")
    parser.add_argument("--location", help="artifact location (default git+<origin>@<HEAD>)")
    parser.add_argument("--run", help="URI of the pipeline run")
    parser.add_argument("--output", type=Path, help="write the bundle here instead of stdout")
    parser.add_argument("--version", action="version", version=version("ois-validator"))
    args = parser.parse_args(argv)

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
        print(f"ois-junit: {error}", file=sys.stderr)
        return EXIT_USAGE

    result = validate(bundle)
    if not result.conforms:
        for report in result.reports:
            print(
                f"ois-junit: {report.level} [{report.rule}] {report.path}: {report.message}",
                file=sys.stderr,
            )
        return EXIT_INVALID

    text = json.dumps(bundle, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return EXIT_OK


def _read(name: str) -> JUnitReport:
    if name == "-":
        return JUnitReport(sys.stdin.buffer.read())
    path = Path(name)
    written_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return JUnitReport(path.read_bytes(), written_at)


if __name__ == "__main__":
    sys.exit(main())
