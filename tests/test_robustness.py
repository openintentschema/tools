"""No rule crashes when a reference names a record of an unexpected type.

Every reference of every valid fixture is pointed at every record in the same document, and at
a criterion of it. Such references keep the schema's reference syntax, so the rules are called
directly; they must report or skip, never raise.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from ois_tools import rules
from ois_tools.document import Document

VALID = sorted((Path(__file__).parent / "fixtures" / "valid").glob("*.json"))


def replace_at(document: Any, path: str, value: str) -> None:
    parts = path[2:].replace("[", ".").replace("]", "").split(".")
    node = document
    for part in parts[:-1]:
        node = node[int(part)] if part.isdigit() else node[part]
    last = parts[-1]
    node[int(last) if last.isdigit() else last] = value


@pytest.mark.parametrize("path", VALID, ids=[p.name for p in VALID])
def test_references_to_any_record_never_crash(path: Path) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    parsed = Document.of(document)
    targets = sorted(
        e.record["id"] + (f"@{e.record['version']}" if "version" in e.record else "")
        for e in parsed.entries
    )
    targets += [f"{t}#AC-1" for t in targets if "@" in t]
    for reference in parsed.references():
        for target in targets:
            mutated = copy.deepcopy(document)
            replace_at(mutated, reference.path, target)
            list(rules.check(Document.of(mutated)))
