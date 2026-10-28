"""Command line interface: ois-passport build INPUT... (see --help)"""

import argparse
import json
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from ois_validator.authoring import AuthoringError, load
from ois_validator.passports import PassportError, Release, build, collect
from ois_validator.validator import validate

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2

SUFFIXES = (".json", ".yaml", ".yml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ois-passport",
        description="Assemble a Quality Passport (specification section 8) from OIS records.",
    )
    parser.add_argument("--version", action="version", version=version("ois-validator"))
    commands = parser.add_subparsers(dest="command", required=True)

    building = commands.add_parser("build", help="assemble an unsigned passport")
    building.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        metavar="INPUT",
        help="record or bundle files, or directories such as .ois/",
    )
    building.add_argument("--release-name", required=True)
    building.add_argument("--release-version", required=True)
    building.add_argument("--release-location", help="URI of the release")
    building.add_argument("--build", required=True, help="id of the released build artifact")
    building.add_argument("--issuer", required=True, help="reference of the issuing actor")
    building.add_argument(
        "--intent",
        action="append",
        dest="intents",
        help="versioned intent to list (default: latest approved of each)",
    )
    building.add_argument("--scope", action="append", help="criterion reference in scope")
    building.add_argument("--subject-name", help="name of the statement subject (default: release)")
    building.add_argument(
        "--approve-release-by",
        help="add a release decision by this actor if the records carry none",
    )
    building.add_argument("--release-outcome", choices=("approved", "passed"), default="approved")
    building.add_argument("--release-decision-id")
    building.add_argument("--at", help="issuedAt, RFC 3339 (default now)")
    building.add_argument("--output", type=Path, help="write the passport here instead of stdout")
    args = parser.parse_args(argv)

    try:
        documents = [load(path) for path in _files(args.inputs)]
        issued_at = _moment(args.at)
    except (OSError, AuthoringError, ValueError) as error:
        print(f"ois-passport: {error}", file=sys.stderr)
        return EXIT_USAGE
    try:
        passport = build(
            collect(documents),
            release=Release(args.release_name, args.release_version, args.release_location),
            build=args.build,
            issued_by=args.issuer,
            issued_at=issued_at,
            intents=args.intents,
            scope=args.scope,
            subject_name=args.subject_name,
            approve_release_by=args.approve_release_by,
            release_outcome=args.release_outcome,
            release_decision_id=args.release_decision_id,
        )
    except PassportError as error:
        print(f"ois-passport: {error}", file=sys.stderr)
        return EXIT_INVALID

    for report in validate(passport).reports:
        print(
            f"ois-passport: {report.level} [{report.rule}] {report.path}: {report.message}",
            file=sys.stderr,
        )
    _emit(passport, args.output)
    return EXIT_OK


def _files(inputs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in inputs:
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*") if p.suffix in SUFFIXES and p.is_file()))
        else:
            files.append(path)
    return files


def _emit(passport: dict[str, Any], path: Path | None) -> None:
    text = json.dumps(passport, indent=2, ensure_ascii=False) + "\n"
    if path is None:
        sys.stdout.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def _moment(text: str | None) -> datetime:
    if text is None:
        return datetime.now(UTC)
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError(f"{text} has no time zone offset")
    return moment
