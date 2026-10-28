"""Copy schema, examples and fixtures from a checkout of the specification repository.

    uv run scripts/sync_spec.py [SPEC_DIR]          # copy and record the commit in spec.lock
    uv run scripts/sync_spec.py --check SPEC_DIR    # fail if a copy differs from SPEC_DIR

SPEC_DIR defaults to ../openintentschema. Copying requires a clean checkout, so that spec.lock
names a commit that holds exactly what was copied.
"""

import argparse
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "spec.lock"
REPOSITORY = "https://github.com/openintentschema/openintentschema"

# source in the spec repository -> copy in this repository
FILES = {"schema/ois.schema.json": "src/ois_validator/schemas/ois-0.1.schema.json"}
TREES = {"examples": "tests/examples", "fixtures": "tests/fixtures"}


def pairs(spec: Path) -> dict[Path, Path]:
    """Every copy in this repository mapped to its source in the spec checkout."""
    result = {ROOT / copy: spec / source for source, copy in FILES.items()}
    for source, copy in TREES.items():
        for path in sorted((spec / source).rglob("*.json")):
            result[ROOT / copy / path.relative_to(spec / source)] = path
    return result


def check(spec: Path) -> list[str]:
    expected = pairs(spec)
    problems = [
        f"{copy.relative_to(ROOT)}: differs from the specification"
        for copy, source in expected.items()
        if not copy.exists() or copy.read_bytes() != source.read_bytes()
    ]
    for tree in TREES.values():
        for stray in sorted((ROOT / tree).rglob("*.json")):
            if stray not in expected:
                problems.append(f"{stray.relative_to(ROOT)}: not in the specification")
    return problems


def head(spec: Path) -> str:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(spec), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    if git("status", "--porcelain"):
        sys.exit(f"{spec} has uncommitted changes; commit them first")
    return git("rev-parse", "HEAD")


def sync(spec: Path) -> None:
    commit = head(spec)
    for tree in TREES.values():
        shutil.rmtree(ROOT / tree, ignore_errors=True)
    for copy, source in pairs(spec).items():
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, copy)
    LOCK.write_text(f'repository = "{REPOSITORY}"\ncommit = "{commit}"\n', encoding="utf-8")
    print(f"copied {len(pairs(spec))} files from {commit}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("spec", nargs="?", type=Path, default=ROOT.parent / "openintentschema")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    spec = args.spec.resolve()
    if args.check:
        locked = tomllib.loads(LOCK.read_text(encoding="utf-8"))["commit"]
        problems = check(spec)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            print(f"copies do not match {REPOSITORY} at {locked}", file=sys.stderr)
        return 1 if problems else 0
    sync(spec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
