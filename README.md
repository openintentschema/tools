# ois-validator

Reference validator for the [Open Intent Schema (OIS)](https://github.com/openintentschema/openintentschema).
It checks OIS records, bundles and Quality Passports against the specification and reports
every problem with its rule ID and level (spec section 11.1):

- **violation**: a MUST is broken; the input does not conform.
- **warning**: R8, R12 and R13 ask for a report; the input still conforms.
- **info**: a SHOULD is not followed.

Status: pre-alpha. The schema check (Appendix B) works. The integrity rules R1–R5 and R8–R15,
digests (JCS, RFC 8785) and DSSE signatures follow.

## Usage

```bash
uv run ois-validator bundle.json passport.json
uv run ois-validator --format json finding.json
cat bundle.json | uv run ois-validator -
```

The input is one JSON document per file: a single record, a bundle, or a passport as an in-toto
Statement v1. Exit code 0 means every file conforms, 1 means at least one violation, 2 means a
file could not be read.

From Python:

```python
from ois_validator import validate

result = validate(document)
result.conforms  # False if any report is a violation
result.reports  # list of Report(level, rule, message, path)
```

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
uv run mypy src tests
```

The schema in `src/ois_validator/schemas/` and the examples in `tests/examples/` are copies from
the specification repository. Update them together with the specification and never edit them
here.

## License

Apache-2.0. Commits need a DCO sign-off (`git commit -s`).
