"""Command line interface: ois <command> [ARGS...]; each command parses its own arguments."""

import sys
from collections.abc import Callable
from importlib.metadata import version

from ois_tools import (
    coverage_cli,
    cucumber_cli,
    intent_cli,
    junit_cli,
    key_cli,
    passport_cli,
    record_cli,
    sign_cli,
    trust_cli,
    validate_cli,
)

EXIT_USAGE = 2

COMMANDS: dict[str, tuple[str, Callable[[list[str]], int]]] = {
    "validate": (
        "check records, bundles and passports against the specification",
        validate_cli.main,
    ),
    "junit": ("turn JUnit XML into a Level 1 bundle", junit_cli.main),
    "cucumber": ("record a Cucumber run as evidence for an intent", cucumber_cli.main),
    "intent": ("seal, approve, version and compare intents", intent_cli.main),
    "record": ("write actors, artifacts, evidence, decisions and findings", record_cli.main),
    "passport": ("assemble a Quality Passport for a release", passport_cli.main),
    "coverage": (
        "show intent coverage per criterion, check what a passport declares",
        coverage_cli.main,
    ),
    "sign": ("sign a bundle or passport with DSSE, or add a signature", sign_cli.main),
    "key": ("create signing keys and show their key ids", key_cli.main),
    "trust": ("fetch a Sigstore trusted root for keyless signatures (network)", trust_cli.main),
}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(_usage(), file=sys.stderr)
        return EXIT_USAGE
    name, rest = args[0], args[1:]
    if name in ("-h", "--help"):
        print(_usage())
        return 0
    if name == "--version":
        print(version("ois-tools"))
        return 0
    command = COMMANDS.get(name)
    if command is None:
        print(f"ois: unknown command {name!r}\n\n{_usage()}", file=sys.stderr)
        return EXIT_USAGE
    return command[1](rest)


def _usage() -> str:
    width = max(len(name) for name in COMMANDS)
    lines = [f"  {name:<{width}}  {summary}" for name, (summary, _) in COMMANDS.items()]
    return "\n".join(
        [
            "usage: ois <command> [ARGS...]",
            "",
            "Tools for the Open Intent Schema (OIS). Commands:",
            "",
            *lines,
            "",
            "ois <command> --help shows the options of a command; ois --version the version.",
        ]
    )


if __name__ == "__main__":
    sys.exit(main())
