"""Command line interface: ois intent seal | approve | next | compare | from-gherkin"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ois_tools.authoring import AuthoringError, load
from ois_tools.cucumber import KEY_PATTERN, CucumberError, draft_intent
from ois_tools.intents import IntentError, approve, compare, file_stem, next_version, seal

EXIT_OK = 0
EXIT_ERROR = 2

MEANING = {
    "unchanged": "unchanged, evidence carries over",
    "changed": "changed, evidence is stale",
    "added": "added, needs evidence",
    "removed": "removed",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ois intent",
        description="Seal, approve and version OIS intents written in YAML or JSON.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    sealing = commands.add_parser("seal", help="compute criterion and intent digests")
    sealing.add_argument("file", type=Path)
    sealing.add_argument("--output", type=Path, help="write here instead of stdout")

    approving = commands.add_parser(
        "approve", help="approve the intent and write it with its intent-approval decision"
    )
    approving.add_argument("file", type=Path)
    approving.add_argument("--by", required=True, help="reference of the approving actor")
    approving.add_argument("--at", help="time of the decision, RFC 3339 (default now)")
    approving.add_argument("--decision-id", help="id of the decision (default derived)")
    approving.add_argument("--policy", help="URI of the policy evaluated")
    approving.add_argument("--policy-version")
    approving.add_argument("--output-dir", type=Path, default=Path(".ois"))

    following = commands.add_parser("next", help="write a draft of the next version")
    following.add_argument("file", type=Path)
    following.add_argument("--at", help="createdAt of the new version, RFC 3339 (default now)")
    following.add_argument("--output-dir", type=Path, default=Path(".ois"))

    gherkin = commands.add_parser(
        "from-gherkin", help="write a draft intent from Gherkin feature files"
    )
    gherkin.add_argument("features", nargs="+", type=Path, metavar="FEATURE")
    gherkin.add_argument("--id", required=True, help="id of the intent")
    gherkin.add_argument("--title", help="default: the feature's name")
    gherkin.add_argument("--produced-by", metavar="ACTOR")
    gherkin.add_argument(
        "--key-pattern", default=KEY_PATTERN, help="regex on a tag without @; group 1 is the key"
    )
    gherkin.add_argument("--at", help="createdAt, RFC 3339 (default now)")
    gherkin.add_argument("--output-dir", type=Path, default=Path(".ois"))

    comparing = commands.add_parser("compare", help="show which criteria changed between versions")
    comparing.add_argument("old", type=Path)
    comparing.add_argument("new", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "seal":
            _emit(seal(load(args.file)), args.output)
        elif args.command == "approve":
            approved, decision = approve(
                load(args.file),
                decided_by=args.by,
                decided_at=_moment(args.at),
                decision_id=args.decision_id,
                policy=args.policy,
                policy_version=args.policy_version,
            )
            stem = file_stem(approved)
            _emit(approved, args.output_dir / "intents" / f"{stem}.ois.json")
            _emit(decision, args.output_dir / "decisions" / f"{stem}-approval.ois.json")
        elif args.command == "from-gherkin":
            skipped: list[str] = []
            draft = draft_intent(
                [path.read_text(encoding="utf-8") for path in args.features],
                id=args.id,
                created_at=_moment(args.at),
                title=args.title,
                produced_by=args.produced_by,
                key_pattern=args.key_pattern,
                skipped=skipped,
            )
            for item in skipped:
                print(f"ois intent: skipped {item}", file=sys.stderr)
            _emit(draft, args.output_dir / "intents" / f"{file_stem(draft)}.ois.yaml")
        elif args.command == "next":
            draft = next_version(load(args.file), created_at=_moment(args.at))
            _emit(draft, args.output_dir / "intents" / f"{file_stem(draft)}.ois.yaml")
        else:
            for key, change in compare(load(args.old), load(args.new)):
                print(f"{key}: {MEANING[change]}")
    except (OSError, AuthoringError, IntentError, CucumberError) as error:
        print(f"ois intent: {error}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _emit(record: dict[str, Any], path: Path | None) -> None:
    if path is not None and path.suffix == ".yaml":
        text = yaml.safe_dump(record, sort_keys=False, allow_unicode=True, width=100)
    else:
        text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    if path is None:
        sys.stdout.write(text)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(path)


def _moment(text: str | None) -> datetime:
    if text is None:
        return datetime.now(UTC)
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise IntentError(f"{text} has no time zone offset")
    return moment
