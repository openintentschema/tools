"""Command line interface: ois-validator FILE..."""

import argparse
import json
import sys
from pathlib import Path

from ois_validator.report import Level, Report, Result
from ois_validator.schema import validate

EXIT_CONFORMS = 0
EXIT_VIOLATION = 1
EXIT_USAGE = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ois-validator",
        description="Validate OIS records, bundles and Quality Passports.",
    )
    parser.add_argument("files", nargs="+", metavar="FILE", help="JSON file, or - for stdin")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    results: dict[str, Result] = {}
    for name in args.files:
        try:
            text = sys.stdin.read() if name == "-" else Path(name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            print(f"ois-validator: {name}: {error}", file=sys.stderr)
            return EXIT_USAGE
        results[name] = _validate_text(text)

    if args.format == "json":
        out = [
            {"file": name, "conforms": r.conforms, "reports": [x.as_dict() for x in r.reports]}
            for name, r in results.items()
        ]
        print(json.dumps(out, indent=2))
    else:
        for name, result in results.items():
            if not result.reports:
                print(f"{name}: conforms")
            for report in result.reports:
                print(f"{name}: {report.level} [{report.rule}] {report.path}: {report.message}")

    return EXIT_CONFORMS if all(r.conforms for r in results.values()) else EXIT_VIOLATION


def _validate_text(text: str) -> Result:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        return Result([Report(Level.VIOLATION, "json", f"not valid JSON: {error}", "$")])
    return validate(document)


if __name__ == "__main__":
    sys.exit(main())
