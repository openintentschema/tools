"""Command line interface: ois coverage PASSPORT | ois coverage INPUT... --build REF

Shows per criterion what counts and why other evidence does not; for a passport, checks the declared
coverage."""

import argparse
import sys
from pathlib import Path

from ois_tools.authoring import AuthoringError, files, load
from ois_tools.coverage import METHOD, Coverage, CoverageError, coverage, passport_coverage
from ois_tools.keyless import is_sigstore_bundle, load_trusted_roots, open_sigstore_bundle
from ois_tools.passports import PassportError, collect, latest_approved
from ois_tools.schema import STATEMENT_TYPE
from ois_tools.signing import SigningError, is_envelope, load_trusted, open_envelope

EXIT_OK = 0
EXIT_DIFFERS = 1
EXIT_USAGE = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ois coverage",
        description="Intent coverage by the reference method of Appendix D, per criterion, and "
        "a check of the coverage a passport declares.",
    )
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
    parser.add_argument(
        "--format", choices=("text", "markdown"), default="text",
        help="markdown: tables for a CI job summary or a pull request comment",
    )  # fmt: skip
    parser.add_argument(
        "--trusted-key",
        action="append",
        default=[],
        metavar="KEY",
        help="public key for a signed passport (attested coverage); a directory, or KEYID=FILE",
    )
    parser.add_argument(
        "--trusted-root",
        action="append",
        default=[],
        metavar="FILE",
        help="Sigstore trusted root for a keyless signed passport (attested coverage)",
    )
    args = parser.parse_args(argv)

    try:
        documents = [load(path) for path in files(args.inputs)]
        single = documents[0] if len(documents) == 1 else None
        statement = single
        if isinstance(single, dict) and is_envelope(single):
            statement = open_envelope(single, []).payload
        elif isinstance(single, dict) and is_sigstore_bundle(single):
            statement = open_sigstore_bundle(single, []).payload
        if isinstance(statement, dict) and statement.get("_type") == STATEMENT_TYPE:
            assert isinstance(single, dict)
            result = passport_coverage(
                single, load_trusted(args.trusted_key), load_trusted_roots(args.trusted_root)
            )
            declared = {
                a["name"]: a["value"]
                for a in statement["predicate"].get("assessments", [])
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
    except (OSError, AuthoringError, CoverageError, PassportError, SigningError) as error:
        print(f"ois coverage: {error}", file=sys.stderr)
        return EXIT_USAGE

    differs = [
        m["name"] for m in result.measurements()
        if m["name"] in declared and not _agrees(declared[m["name"]], m["value"])
    ]  # fmt: skip
    if args.format == "markdown":
        print(markdown(result, declared, differs), end="")
    else:
        _show(result)
        for measurement in result.measurements():
            name, value = measurement["name"], measurement["value"]
            print(f"{name} {value}" + (f" (declared {declared[name]})" if name in declared else ""))
    for name in differs:
        computed = next(m["value"] for m in result.measurements() if m["name"] == name)
        print(
            f"ois coverage: declared {name} {declared[name]}, computed {computed}", file=sys.stderr
        )
    return EXIT_DIFFERS if differs else EXIT_OK


def markdown(result: Coverage, declared: dict[str, object], differs: list[str]) -> str:
    """The coverage and each criterion as Markdown tables; ids shortened to their last part."""
    lines = ["### Intent coverage", ""]
    if not result.criteria:
        return "\n".join([*lines, "No criteria in scope.", ""])
    lines += ["| Measure | Value | Declared |", "| --- | --- | --- |"]
    for measurement in result.measurements():
        name = measurement["name"]
        stated = f"{_percent(declared[name])}" if name in declared else ""
        stated += ", differs" if name in differs else ""
        lines.append(f"| {name} | {_percent(measurement['value'])} | {stated} |")
    lines += ["", "| Criterion | Statement | Status | Evidence |", "| --- | --- | --- | --- |"]
    for criterion in result.criteria:
        statement = cell(criterion.statement)
        statement = statement if len(statement) <= 80 else statement[:79].rstrip() + "…"
        if criterion.covered:
            status, evidence = "covered", ", ".join(f"`{_short(e)}`" for e in criterion.counted)
        else:
            status = "not covered"
            evidence = "; ".join(f"`{_short(e)}`: {cell(why)}" for e, why in criterion.reasons)
        lines.append(
            f"| `{_short(criterion.reference)}` | {statement} | {status} | "
            f"{evidence or 'no evidence'} |"
        )
    return "\n".join([*lines, ""])


def cell(text: str) -> str:
    """Text safe inside a Markdown table cell."""
    return " ".join(text.replace("|", "\\|").split())


def _short(reference: str) -> str:
    return reference.rstrip("/").rsplit("/", 1)[-1]


def _percent(value: object) -> str:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return str(value)
    return f"{value * 100:.4g} %"


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
