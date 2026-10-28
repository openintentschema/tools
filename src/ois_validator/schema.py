"""Schema check against the normative JSON Schema of the specification (Appendix B).

The schema accepts a record or a bundle at its root and answers a broken record inside a
bundle with a bare `oneOf` failure. To report something useful, every record is checked
against the definition for its own `type`, and the bundle or passport predicate around it
only for its own members.
"""

import json
from collections.abc import Iterable, Iterator
from functools import cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, best_match
from jsonschema.protocols import Validator
from referencing import Registry, Resource

from ois_validator.report import Level, Report, Result

SCHEMA_FILE = "ois-0.1.schema.json"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PASSPORT_PREDICATE_TYPE = "https://ois.example/passport/v0.1"  # placeholder until the domain is set
RECORD_TYPES = ("intent", "artifact", "actor", "evidence", "decision", "finding")


@cache
def schema() -> dict[str, Any]:
    text = files("ois_validator").joinpath("schemas", SCHEMA_FILE).read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


@cache
def _validator(definition: str) -> Validator:
    root = schema()
    registry: Registry[Any] = Registry().with_resource(root["$id"], Resource.from_contents(root))
    return Draft202012Validator(
        {"$ref": f"{root['$id']}#/$defs/{definition}"},
        registry=registry,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def check(document: object) -> Result:
    """Check one parsed document against Appendix B: a record, a bundle or a passport statement."""
    result = Result()
    if isinstance(document, dict) and document.get("_type") == STATEMENT_TYPE:
        _check_statement(document, result)
    elif isinstance(document, dict) and document.get("type") == "bundle":
        _check_container("bundle", document, "$", result)
    else:
        _check_record(document, "$", result)
    return result


def _check_statement(statement: dict[str, Any], result: Result) -> None:
    if statement.get("predicateType") != PASSPORT_PREDICATE_TYPE:
        result.reports.append(
            _violation(f"predicateType must be {PASSPORT_PREDICATE_TYPE!r}", "$.predicateType")
        )
        return
    _check_container("passportPredicate", statement.get("predicate"), "$.predicate", result)


def _check_container(definition: str, instance: object, path: str, result: Result) -> None:
    # Errors inside records[] are left to the per-record check below.
    errors = (
        e
        for e in _validator(definition).iter_errors(instance)
        if len(e.absolute_path) < 2 or e.absolute_path[0] != "records"
    )
    result.reports.extend(_reports(errors, definition, path))
    if isinstance(instance, dict) and isinstance(instance.get("records"), list):
        for index, record in enumerate(instance["records"]):
            _check_record(record, f"{path}.records[{index}]", result)


def _check_record(record: object, path: str, result: Result) -> None:
    kind = record.get("type") if isinstance(record, dict) else None
    if kind not in RECORD_TYPES:
        expected = ", ".join(RECORD_TYPES)
        result.reports.append(_violation(f"type must be one of {expected}", f"{path}.type"))
        return
    result.reports.extend(_reports(_validator(kind).iter_errors(record), kind, path))


def _reports(errors: Iterable[ValidationError], definition: str, path: str) -> Iterator[Report]:
    for error in sorted(errors, key=lambda e: [str(p) for p in e.absolute_path]):
        leaf = _most_specific(error)
        message = leaf.message
        if leaf.validator == "unevaluatedProperties" and not leaf.absolute_path:
            # A failing allOf branch evaluates none of its properties, so 2020-12 also calls the
            # valid common fields unexpected. Report only the names the definition lacks.
            unexpected = sorted(set(leaf.instance) - _declared(definition))
            if not unexpected:
                continue
            names = ", ".join(repr(name) for name in unexpected)
            message = f"Unevaluated properties are not allowed ({names} unexpected)"
        yield _violation(message, _join(path, leaf.absolute_path))


@cache
def _declared(definition: str) -> frozenset[str]:
    defs = schema()["$defs"]
    return frozenset(defs[definition].get("properties", {})) | frozenset(
        defs["common"]["properties"]
    )


def _most_specific(error: ValidationError) -> ValidationError:
    while error.validator in ("oneOf", "anyOf") and error.context:
        match = best_match(error.context)  # type: ignore[no-untyped-call]
        if match is None:
            break
        error = match
    return error


def _join(path: str, parts: Iterable[str | int]) -> str:
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def _violation(message: str, path: str) -> Report:
    return Report(Level.VIOLATION, "schema", message, path)
