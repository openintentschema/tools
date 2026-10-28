"""Read authored records: JSON, or YAML as specification section 7 allows it.

YAML must convert to JSON without loss, so it is read with the JSON schema of YAML 1.2:
`2026-10-14T09:12:00Z` stays a string, `yes` and `on` stay strings, `0123` is 123. Features with no
JSON equivalent are refused: tags, merge keys, non-string keys and duplicate keys. Otherwise a
digest would depend on what a YAML library made of the text.
"""

import json
import re
from pathlib import Path
from typing import Any

import yaml


class AuthoringError(ValueError):
    """The text is not JSON, or YAML that converts to JSON without loss."""


class _Loader(yaml.SafeLoader):
    pass


_Loader.yaml_implicit_resolvers = {}
for _tag, _pattern, _first in (
    ("null", r"^(?:~|null|Null|NULL|)$", ["~", "n", "N", ""]),
    ("bool", r"^(?:true|True|TRUE|false|False|FALSE)$", list("tTfF")),
    ("int", r"^[-+]?[0-9]+$", list("-+0123456789")),
    (
        "float",
        r"^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$",
        list("-+.0123456789"),
    ),
):
    _Loader.add_implicit_resolver(f"tag:yaml.org,2002:{_tag}", re.compile(_pattern), _first)


def _decimal_int(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> int:
    return int(loader.construct_scalar(node))


def _decimal_float(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> float:
    return float(loader.construct_scalar(node))


_Loader.add_constructor("tag:yaml.org,2002:int", _decimal_int)
_Loader.add_constructor("tag:yaml.org,2002:float", _decimal_float)

_STR = "tag:yaml.org,2002:str"


def loads_yaml(text: str) -> Any:
    try:
        for event in yaml.parse(text, Loader=_Loader):
            tag = getattr(event, "tag", None)
            if tag is not None:
                raise AuthoringError(
                    f"tag {tag} at {event.start_mark.line + 1}:{event.start_mark.column + 1}: "
                    "tags have no JSON equivalent (specification section 7)"
                )
        node = yaml.compose(text, Loader=_Loader)
        if node is not None:
            _check(node)
        return yaml.load(text, Loader=_Loader)  # _Loader derives from SafeLoader
    except yaml.YAMLError as error:
        raise AuthoringError(f"not valid YAML: {error}") from error


def _check(node: yaml.Node) -> None:
    if isinstance(node, yaml.MappingNode):
        seen: set[str] = set()
        for key, value in node.value:
            where = f"{key.start_mark.line + 1}:{key.start_mark.column + 1}"
            if key.tag != _STR:
                raise AuthoringError(f"key at {where} is not a string (specification section 7)")
            if key.value == "<<" and key.style is None:
                raise AuthoringError(f"merge key at {where} has no JSON equivalent")
            if key.value in seen:
                raise AuthoringError(f"duplicate key {key.value!r} at {where}")
            seen.add(key.value)
            _check(value)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _check(item)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise AuthoringError(f"duplicate keys: {', '.join(duplicates)}")
    return dict(pairs)


def loads(text: str, *, json_only: bool = False) -> Any:
    """Parse JSON, or YAML unless `json_only`."""
    if json_only:
        try:
            return json.loads(text, object_pairs_hook=_no_duplicates)
        except json.JSONDecodeError as error:
            raise AuthoringError(f"not valid JSON: {error}") from error
    return loads_yaml(text)


def load(path: Path) -> Any:
    """A `.json` file as JSON; anything else as YAML, which also reads JSON."""
    return loads(path.read_text(encoding="utf-8"), json_only=path.suffix == ".json")


SUFFIXES = (".json", ".yaml", ".yml")


def files(inputs: list[Path]) -> list[Path]:
    """Files as given, and in directories every JSON or YAML file, recursively and sorted."""
    found: list[Path] = []
    for path in inputs:
        if path.is_dir():
            found.extend(sorted(p for p in path.rglob("*") if p.suffix in SUFFIXES and p.is_file()))
        else:
            found.append(path)
    return found
