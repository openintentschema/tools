"""Command line interface: ois trust update [--tuf-url URL --tuf-root FILE] --output FILE

Fetches a Sigstore trusted root through TUF; the only command with network access."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ois_tools.keyless import INSTALL

PUBLIC_GOOD = "https://tuf-repo-cdn.sigstore.dev"

EXIT_OK = 0
EXIT_USAGE = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ois trust",
        description="Fetch Sigstore trusted roots for verifying keyless signatures.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    updating = commands.add_parser("update", help="fetch a trusted root through TUF (network)")
    updating.add_argument(
        "--tuf-url",
        default=PUBLIC_GOOD,
        help=f"TUF repository (default: public good, {PUBLIC_GOOD})",
    )
    updating.add_argument(
        "--tuf-root",
        type=Path,
        metavar="FILE",
        help="TUF root metadata of an own instance, first use",
    )
    updating.add_argument("--output", required=True, type=Path, help="trusted root file to write")
    args = parser.parse_args(argv)

    try:
        config = _from_tuf(args.tuf_url, offline=False, bootstrap_root=args.tuf_root)
        root = json.loads(config.trusted_root._inner.to_json())
    except Exception as error:  # network, TUF and library errors alike: nothing is written
        print(
            f"ois trust: cannot fetch the trusted root from {args.tuf_url}: {error}",
            file=sys.stderr,
        )
        return EXIT_USAGE
    text = json.dumps(root, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    authorities = len(root.get("certificateAuthorities", []))
    logs = len(root.get("tlogs", []))
    print(
        f"ois trust: wrote {args.output}: {authorities} certificate authorities, "
        f"{logs} transparency logs from {args.tuf_url}",
        file=sys.stderr,
    )
    return EXIT_OK


def _from_tuf(url: str, offline: bool = False, bootstrap_root: Path | None = None) -> Any:
    try:
        from sigstore.models import ClientTrustConfig  # optional dependency
    except ImportError as error:
        raise RuntimeError(INSTALL) from error
    return ClientTrustConfig.from_tuf(url, offline=offline, bootstrap_root=bootstrap_root)


if __name__ == "__main__":
    sys.exit(main())
