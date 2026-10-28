# ois-validator

[![PyPI](https://img.shields.io/pypi/v/ois-validator)](https://pypi.org/project/ois-validator/)
[![Checks](https://github.com/openintentschema/validator/actions/workflows/checks.yml/badge.svg)](https://github.com/openintentschema/validator/actions/workflows/checks.yml)

Reference validator for the [Open Intent Schema (OIS)](https://github.com/openintentschema/openintentschema),
the open data standard that links what was required to what was built and to the evidence in
between. It checks OIS records, bundles and Quality Passports and reports every problem with its
rule and level (specification section 11.1):

- **violation:** a MUST is broken; the input does not conform.
- **warning:** R8, R12 and R13 ask for a report; the input still conforms.
- **info:** a SHOULD is not followed.

The package also brings `ois-junit`, which turns the JUnit XML of an existing pipeline into OIS
records.

**Status:** pre-alpha, targets specification v0.1. The validator checks Appendix B and every rule
of section 5 that can be checked from the records at hand (R1–R5, R8–R17) and passes all
conformance fixtures. Signatures are not verified yet, so R12 reports every recorded context as
self-declared.

## Install

```bash
uvx ois-validator bundle.json          # run without installing
pip install ois-validator              # or install; Python 3.13 or later
```

## Validate

```bash
ois-validator bundle.json passport.json
ois-validator --format json finding.json
cat bundle.json | ois-validator -
```

Each file holds one JSON document: a single record, a bundle, or a passport as an in-toto
Statement v1. Exit code 0 means every file conforms, 1 means at least one violation, 2 means a
file could not be read. Reports point at the field concerned:

```
same-producer.json: violation [R5] $.records[12].derivedFrom: test and subject are both produced by https://ois.acme.example/actors/dev-agent
```

With `--format json`:

```json
[
  {
    "file": "same-producer.json",
    "conforms": false,
    "reports": [
      {
        "level": "violation",
        "rule": "R5",
        "message": "test and subject are both produced by https://ois.acme.example/actors/dev-agent",
        "path": "$.records[12].derivedFrom"
      }
    ]
  }
]
```

From Python:

```python
from ois_validator import validate

result = validate(document)  # a parsed JSON document
result.conforms  # False if any report is a violation
result.reports  # list of Report(level, rule, message, path)
```

## Record test results from JUnit XML

`ois-junit` turns the JUnit XML that almost every test framework writes into a bundle, as the
Level 1 quick start in specification section 11.4 describes. Run it in the Git checkout the
tests ran in:

```bash
ois-junit build/test-results/*.xml \
  --namespace https://ois.acme.example \
  --suite "Team Billing" \
  --run "$CI_PIPELINE_URL" \
  --output ois-bundle.json
```

| Option | Meaning |
| --- | --- |
| `--namespace` | URI under your organization's domain; every record id starts with it. Never requested. |
| `--suite` | Team or suite that owns the tests; names the test suite actor. |
| `--repo` | Git working tree the tests ran in (default `.`). |
| `--location` | Artifact location (default `git+<origin>@<HEAD>`, credentials removed). |
| `--run` | URI of the pipeline run, linked from every evidence record. |
| `--output` | Write the bundle to a file instead of standard output. |

The bundle holds an actor for the adapter and one for the test suite, a characterization intent
in draft with one criterion per test case, an artifact per test file (pinned by `sha256` and
`gitCommit`) and an evidence record per result with `derivedFrom: unknown`. `ois-junit`
validates the bundle before writing it: exit code 0 writes it, 1 means it would not conform and
nothing is written, 2 means a report or the Git checkout could not be read.

The [getting started guide](https://github.com/openintentschema/openintentschema/blob/main/site/getting-started.md)
walks through it step by step, including a CI example.

## Documentation

- [Specification, schema and conformance fixtures](https://github.com/openintentschema/openintentschema)
- [Getting started](https://github.com/openintentschema/openintentschema/blob/main/site/getting-started.md): first OIS bundle from JUnit XML
- [Architecture](docs/architecture.md): how the validator and `ois-junit` are built, and why
- [Development](docs/development.md): setup, checks, specification sync, adding a rule, releases
- [Changelog](CHANGELOG.md)

## Contributing

What the validator accepts is decided in the specification repository; bug fixes, tooling and
documentation are welcome here. See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues:
[SECURITY.md](SECURITY.md).

## License

Apache-2.0.
