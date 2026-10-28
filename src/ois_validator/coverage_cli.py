"""Command line interface: ois-coverage PASSPORT | ois-coverage INPUT... --build REF

Shows per criterion whether it is covered and, if not, why each piece of evidence does not count.
For a passport it also compares the coverage the passport declares with the computed one.
"""

import argparse
import sys
from importlib.metadata import version
from pathlib import Path

from ois_validator.authoring import AuthoringError, files, load
from ois_validator.coverage import METHOD, Coverage, CoverageError, coverage, passport_coverage
from ois_validator.passports import PassportError, collect, latest_approved
from ois_validator.schema import STATEMENT_TYPE

EXIT_OK = 0
EXIT_DIFFERS = 1
EXIT_USAGE = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ois-coverage",
        description="Intent coverage by the reference method of Appendix D, per criterion, and "
        "a check of the coverage a passport declares.",
    )
    parser.add_argument("--version", action="version", version=version("ois-validator"))
    parser.add_argument(
        "inputs", nargs="+", type=Path, metavar="INPUT",
        help="a passport, or record and bundle files and directories such as .ois/",
    )  # fmt: skip
    parser.add_argument("--build", help="release subject for records (a passport names its own)")
    parser.add_argument(
        "--intent", action="append", dest="intents",
        help="versioned intent (default: latest approved of each)",
    )  # fmt: skip
    parser.add_argument("--scope", action="append", help="criterion reference in scope")
    args = parser.parse_args(argv)

    try:
        documents = [load(path) for path in files(args.inputs)]
        single = documents[0] if len(documents) == 1 else None
        if isinstance(single, dict) and single.get("_type") == STATEMENT_TYPE:
            result = passport_coverage(single)
            declared = {
                a["name"]: a["value"]
                for a in single["predicate"].get("assessments", [])
                if a.get("method") == METHOD
            }
        else:
            if args.build is None:
                raise CoverageError("records need --build: the release subject")
            records = collect(documents)
            result = coverage(
                records, subject=args.build, intents=args.intents or latest_approved(records),
                scope=args.scope,
            )  # fmt: skip
            declared = {}
    except (OSError, AuthoringError, CoverageError, PassportError) as error:
        print(f"ois-coverage: {error}", file=sys.stderr)
        return EXIT_USAGE

    _show(result)
    differs = False
    for measurement in result.measurements():
        name, value = measurement["name"], measurement["value"]
        if name not in declared:
            print(f"{name} {value}")
            continue
        print(f"{name} {value} (declared {declared[name]})")
        if not _agrees(declared[name], value):
            print(
                f"ois-coverage: declared {name} {declared[name]}, computed {value}", file=sys.stderr
            )
            differs = True
    return EXIT_DIFFERS if differs else EXIT_OK


def _show(result: Coverage) -> None:
    for criterion in result.criteria:
        if criterion.covered:
            print(f"{criterion.reference}  covered  {', '.join(criterion.counted)}")
            continue
        print(f"{criterion.reference}  not covered")
        if not criterion.reasons:
            print("    no evidence")
        for evidence, why in criterion.reasons:
            print(f"    {evidence}: {why}")


def _agrees(declared: object, computed: float) -> bool:
    """Equal at the precision the passport declares: 0.33 agrees with 0.3333."""
    if not isinstance(declared, int | float) or isinstance(declared, bool):
        return False
    text = repr(float(declared))
    decimals = len(text.partition(".")[2].rstrip("0")) if "e" not in text else 4
    return round(computed, decimals) == round(float(declared), decimals)


if __name__ == "__main__":
    sys.exit(main())
