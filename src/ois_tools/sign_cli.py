"""Command line interface: ois sign FILE --key KEY... [--output FILE]

Wraps a bundle or a passport in a DSSE envelope signed with each key, or adds signatures to an
existing envelope, for example when the runtime that recorded the contexts signs a bundle too
(Level 3, R12). An encrypted private key is opened with the passphrase in $OIS_KEY_PASSPHRASE.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ois_tools.authoring import AuthoringError, load
from ois_tools.signing import SigningError, is_envelope, load_private_key, open_envelope, sign
from ois_tools.validator import validate

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ois sign",
        description="Sign a bundle or a passport with DSSE (specification section 8.3).",
    )
    parser.add_argument("file", type=Path, help="bundle, passport or DSSE envelope (JSON)")
    parser.add_argument(
        "--key", action="append", required=True, type=Path, help="private key (PEM); repeatable"
    )
    parser.add_argument("--output", type=Path, help="write here instead of stdout")
    args = parser.parse_args(argv)

    passphrase = os.environ.get("OIS_KEY_PASSPHRASE")
    try:
        document = load(args.file)
        if not isinstance(document, dict):
            raise SigningError(f"{args.file} holds no JSON object")
        keys = [load_private_key(path.read_bytes(), passphrase.encode() if passphrase else None)
                for path in args.key]  # fmt: skip
    except (OSError, AuthoringError, SigningError) as error:
        print(f"ois sign: {error}", file=sys.stderr)
        return EXIT_USAGE

    content = open_envelope(document, []).payload if is_envelope(document) else document
    violations = [r for r in validate(content).reports if r.level == "violation"]
    if violations:
        for report in violations:
            message = f"{report.level} [{report.rule}] {report.path}: {report.message}"
            print(f"ois sign: {message}", file=sys.stderr)
        print("ois sign: nothing signed: the document does not conform", file=sys.stderr)
        return EXIT_INVALID
    try:
        for key in keys:
            document = sign(document, key)
    except SigningError as error:
        print(f"ois sign: {error}", file=sys.stderr)
        return EXIT_USAGE
    _emit(document, args.output)
    return EXIT_OK


def _emit(envelope: dict[str, Any], path: Path | None) -> None:
    text = json.dumps(envelope, indent=2) + "\n"
    if path is None:
        sys.stdout.write(text)
    else:
        path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
