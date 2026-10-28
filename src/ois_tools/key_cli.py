"""Command line interface: ois key generate NAME | ois key id FILE

The private key is written unencrypted and readable by its owner only."""

import argparse
import os
import sys
from pathlib import Path

from ois_tools.signing import SigningError, generate_key, load_public_key

EXIT_OK = 0
EXIT_USAGE = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ois key", description="Create signing keys and show their key ids."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    generating = commands.add_parser("generate", help="write NAME.key and NAME.pub, print key id")
    generating.add_argument("name", help="file name for the pair, e.g. the actor: agent-runtime")
    generating.add_argument("--output-dir", type=Path, default=Path("."))
    showing = commands.add_parser("id", help="print the key id of a public key")
    showing.add_argument("file", type=Path, help="PEM public key")
    args = parser.parse_args(argv)

    try:
        if args.command == "id":
            print(load_public_key(args.file.read_bytes()).keyid)
            return EXIT_OK
        private_pem, public_pem, keyid = generate_key()
        private = args.output_dir / f"{args.name}.key"
        public = args.output_dir / f"{args.name}.pub"
        if private.exists() or public.exists():
            raise SigningError(f"{private} or {public} exists; keys are never overwritten")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(private_pem)
        public.write_bytes(public_pem)
    except (OSError, SigningError) as error:
        print(f"ois key: {error}", file=sys.stderr)
        return EXIT_USAGE
    print(f"ois key: wrote {private} (keep it secret) and {public}", file=sys.stderr)
    print(keyid)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
