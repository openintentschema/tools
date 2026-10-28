# ois-validator

Reference validator for the [Open Intent Schema (OIS)](https://github.com/openintentschema/openintentschema).
It checks OIS records, bundles and Quality Passports against the specification and reports
every problem with its rule ID and level (spec section 11.1):

- **violation**: a MUST is broken; the input does not conform.
- **warning**: R8, R12 and R13 ask for a report; the input still conforms.
- **info**: a SHOULD is not followed.

Status: pre-alpha. The validator checks Appendix B and every rule of section 5 that can be
checked from the records at hand (R1–R5, R8–R17) and passes all conformance fixtures. Signatures
are not verified yet, so R12 reports every recorded context as self-declared.

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

## Level 1: records from JUnit XML

`ois-junit` turns the JUnit XML that almost every test framework writes (pytest `--junitxml`,
Maven Surefire, Gradle, jest-junit, Playwright, go-junit-report, ...) into a bundle, as the
quick start in spec section 11.4 describes. Run it in the Git checkout the tests ran in:

```bash
ois-junit build/test-results/*.xml \
  --namespace https://ois.acme.example \
  --suite "Team Billing" \
  --run "$CI_PIPELINE_URL" \
  --output ois-bundle.json
```

The bundle holds an actor for the adapter and one for the test suite, a characterization intent
in draft with one criterion per test case, an artifact per test file and an evidence record per
result with `derivedFrom: unknown`. Intent coverage is zero until intents are approved and
tests are derived from them: the honest baseline for an existing system.

- **Ids** live under `--namespace`, a URI under your domain. Criterion keys are `TC-` plus a hash
  of class and test name, so they stay valid and stable for any test name. Artifact ids follow
  path and content, so a changed test file becomes a new artifact.
- **Test files** come from the `file` attribute, or from the class name
  (`com.acme.InvoiceTest` finds `src/test/java/com/acme/InvoiceTest.java`). Their digest is the
  `sha256` of the file at `HEAD` plus `gitCommit`; a test whose file is not found gets
  `gitCommit` alone.
- **Location** is `git+<origin>@<HEAD>` with any credentials removed; `--location` overrides it.
- **Timestamps:** a test suite's `timestamp` with an offset becomes `observedAt`; without an
  offset, which JUnit allows, the time the report file was written is used.

`ois-junit` validates the bundle before writing it and exits with 1 instead of writing an
invalid one.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
uv run mypy src tests scripts
```

The schema in `src/ois_validator/schemas/`, the examples in `tests/examples/` and the conformance
fixtures in `tests/fixtures/` are copies from the
[specification repository](https://github.com/openintentschema/openintentschema). `spec.lock`
names the commit they come from, and CI fails if a copy differs from that commit. Never edit them
here; update them from a clean checkout of the specification:

```bash
uv run scripts/sync_spec.py ../openintentschema
```

`tests/test_fixtures.py` runs every fixture and compares the reports for the rules listed in
`IMPLEMENTED`. Add a rule there once the validator checks it.

## Releases

Set `version` in `pyproject.toml`, merge, then push a tag `v<version>`. The workflow
`release.yml` checks that tag and version match, builds the package, smoke-tests the wheel and
publishes it to PyPI with trusted publishing (no stored token; PyPI records signed attestations).

## License

Apache-2.0. Commits need a DCO sign-off (`git commit -s`).
