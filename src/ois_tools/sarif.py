"""SARIF 2.1.0 for ois validate (GitHub code scanning, IDEs); report paths become line numbers."""

import bisect
import re
from importlib.metadata import version
from typing import Any

from ois_tools.report import Level, Result

SPEC = "https://openintentschema.io/specification/"
RULES = {
    "json": ("Not valid JSON", f"{SPEC}serialization/"),
    "schema": ("Appendix B: the JSON Schema", f"{SPEC}appendix-b-json-schema/"),
    "signature": (
        "DSSE and Sigstore signatures (section 8.3)",
        "https://openintentschema.io/sign/",
    ),
    "R1": ("Approval", None),
    "R2": ("Criterion references", None),
    "R3": ("Evidence targets", None),
    "R4": ("Derivation", None),
    "R5": ("Independence", None),
    "R8": ("Production data", None),
    "R9": ("Waivers", None),
    "R10": ("Resolution", None),
    "R11": ("Revocation", None),
    "R12": ("Context attestation", None),
    "R13": ("Open findings", None),
    "R14": ("Finding chain", None),
    "R15": ("Passport intents", None),
    "R16": ("Release decision (proposed)", None),
    "R17": ("Digests (proposed)", None),
    "regulated/P1": ("Regulated profile: intents carry risk", None),
    "regulated/P2": ("Regulated profile: people approve intents and releases", None),
    "regulated/P3": ("Regulated profile: passports are signed", None),
    "regulated/P4": ("Regulated profile: no production data in tests", None),
}
LEVELS = {Level.VIOLATION: "error", Level.WARNING: "warning", Level.INFO: "note"}


def to_sarif(results: dict[str, tuple[str, Result]]) -> dict[str, Any]:
    """One run over the files, given as name -> (text, result)."""
    rules: list[str] = []
    found = []
    for name, (text, result) in results.items():
        lines = _value_lines(text)
        for report in result.reports:
            if report.rule not in rules:
                rules.append(report.rule)
            found.append(
                {
                    "ruleId": report.rule,
                    "ruleIndex": rules.index(report.rule),
                    "level": LEVELS[report.level],
                    "message": {"text": report.message},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": "stdin" if name == "-" else name},
                                "region": {"startLine": _line(lines, report.path)},
                            },
                            "logicalLocations": [
                                {"fullyQualifiedName": report.path, "kind": "member"}
                            ],
                        }
                    ],
                }
            )
    driver = {
        "name": "ois validate",
        "version": version("ois-tools"),
        "informationUri": "https://openintentschema.io/develop/tools/",
        "rules": [_rule(rule) for rule in rules],
    }
    return {
        "$schema": "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{"tool": {"driver": driver}, "results": found}],
    }


def line_of(text: str, path: str) -> int:
    """The line of the value at a report path, or of its nearest existing parent."""
    return _line(_value_lines(text), path)


def _rule(rule: str) -> dict[str, Any]:
    name, uri = RULES.get(rule, (rule, None))
    if uri is None:
        profile = rule.startswith("regulated/")
        uri = (
            "https://openintentschema.io/profiles/regulated/v0.1/"
            if profile
            else f"{SPEC}relationships-and-integrity-rules/"
        )
    return {"id": rule, "shortDescription": {"text": name}, "helpUri": uri}


def _line(lines: dict[str, int], path: str) -> int:
    """Payload paths of an envelope fall back to the payload; others to their nearest parent."""
    while path:
        if path in lines:
            return lines[path]
        for payload in ("$.payload", "$.dsseEnvelope.payload"):
            if payload in lines and path.startswith(("$.predicate", "$.records", "$.subject")):
                return lines[payload]
        path = re.sub(r"(\.[^.\[\]]+|\[\d+\])$", "", path) if path != "$" else ""
    return 1


def _value_lines(text: str) -> dict[str, int]:
    """The line where each value starts, by JSONPath; empty if the text is not JSON."""
    starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
    found: dict[str, int] = {}

    def line(index: int) -> int:
        return bisect.bisect_right(starts, index)

    def skip(i: int) -> int:
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        return i

    def string(i: int) -> tuple[str, int]:
        j, out = i + 1, []
        while text[j] != '"':
            if text[j] == "\\":
                out.append(text[j : j + 2])
                j += 2
            else:
                out.append(text[j])
                j += 1
        return "".join(out), j + 1

    def value(i: int, path: str) -> int:
        i = skip(i)
        found[path] = line(i)
        if text[i] == "{":
            i = skip(i + 1)
            while text[i] != "}":
                key, i = string(skip(i))
                i = value(skip(i) + 1, f"{path}.{key}")
                i = skip(i)
                if text[i] == ",":
                    i = skip(i + 1)
            return i + 1
        if text[i] == "[":
            i, index = skip(i + 1), 0
            while text[i] != "]":
                i = skip(value(i, f"{path}[{index}]"))
                index += 1
                if text[i] == ",":
                    i = skip(i + 1)
            return i + 1
        if text[i] == '"':
            return string(i)[1]
        match = re.compile(r"-?\d[\d.eE+-]*|true|false|null").match(text, i)
        if match is None:
            raise ValueError(f"unexpected character at {i}")
        return match.end()

    try:
        value(0, "$")
    except (IndexError, ValueError):
        return found
    return found
