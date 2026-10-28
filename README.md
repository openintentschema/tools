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

The package also brings the commands that write OIS records:

- `ois-junit` turns the JUnit XML of an existing pipeline into OIS records;
- `ois-intent` seals, approves and versions intents written in YAML or JSON;
- `ois-record` writes artifacts, actors, evidence, decisions and findings, with their digests;
- `ois-passport` assembles a Quality Passport for a release, with its intent coverage;
- `ois-coverage` shows that coverage per criterion and checks what a passport declares.

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

The [getting started guide](https://openintentschema.io/getting-started/)
walks through it step by step, including a CI example.

## Write and approve intents

`ois-intent` takes an intent written in YAML or JSON and does what is error-prone by hand:

```bash
ois-intent seal INT-142.ois.yaml                      # criterion and intent digests (RFC 8785)
ois-intent approve INT-142.ois.yaml --by https://ois.acme.example/actors/po-7f3a
ois-intent next .ois/intents/INT-142-v1.ois.json      # draft of version 2, superseding version 1
ois-intent compare .ois/intents/INT-142-v1.ois.json .ois/intents/INT-142-v2.ois.yaml
```

- `approve` writes the approved intent and its intent-approval decision, bound by the intent's
  digest (R1), to `.ois/intents/` and `.ois/decisions/` (specification section 7). It refuses an
  approved intent whose content no longer matches its digest: approved intents never change.
- `compare` shows per criterion whether evidence carries over to the new version or is stale
  (section 6.3); `criticality` does not count.
- YAML is read as section 7 requires: `2026-10-14T09:12:00Z` and `yes` stay strings, and tags,
  merge keys and non-string or duplicate keys are refused, so that digests do not depend on how
  a YAML library reads the text.

## Record artifacts, actors, evidence, decisions and findings

`ois-record` writes every other record type, one record per command, to `.ois/<type>s/`. It
computes what is error-prone by hand, validates the record and prints the reference other records
use for it, so a pipeline can pass it on:

```bash
export OIS_NAMESPACE=https://ois.acme.example              # for derived ids, or --namespace

CI=$(ois-record actor --kind tool --name "CI build" --version 7)
DEV=$(ois-record actor --kind agent --name "Dev Agent" --version 2.1 --model acme-ai:coder:2026-09)
CODE=$(ois-record artifact src/invoice.py --kind code --produced-by "$DEV")
BUILD=$(ois-record artifact dist/billing-2026.10.4-py3-none-any.whl --kind build \
  --location pkg:pypi/billing@2026.10.4 --produced-by "$CI" --input "$CODE")
ois-record decision --gate merge --subject "$CODE" --outcome passed --by "$CI"
```

| Command | Writes | Computed for you |
| --- | --- | --- |
| `actor` | a human, agent or tool | id from the name; the reference `<id>@<version>` |
| `artifact FILE` | an artifact of any kind | `sha256`; for a committed file also `gitCommit`, location `git+<origin>@<HEAD>` and path |
| `artifact --commit REPO` | the repository at `HEAD` | `gitCommit` and location |
| `artifact --digest ALG:HEX` | an artifact with a known digest, such as an image | id from name and digest |
| `evidence` | one result of one test for one criterion | id; measures from `--measure responseTimeP95=420ms` |
| `decision` | a gate outcome | `subjectDigest` from the subject among the records (R1, R16) |
| `finding` | a defect or incident | id |
| `supersede FILE` | the next record of a finding chain | id (`FND-18-r2`), `supersedes`, the unchanged fields |
| `bundle INPUT...` | a self-contained bundle, to stdout or `--output` | each record once, validated |

- **Records never change.** Recording the same thing again prints its reference and keeps the
  file; a different record under an existing id is refused (R10). A changed actor needs a new
  `--version`.
- **Files are located in Git.** A file must be committed and unchanged unless `--location` names
  where it lives, for example a Package URL for a build.
- **Rules are checked where the records allow it.** `decision` checks subject and outcome of the
  well-known gates (specification section 4.6) and, for `finding-resolution`, that the deciding
  actor took no part in the resolution (R14). `supersede` refuses a second successor (R14).
  `decision` and `supersede` look up references in the output directory and in `--records`.
- Exit code 0 writes the record, 1 means it would break a rule and nothing is written, 2 means a
  file could not be read or an argument is malformed.

## Build a Quality Passport

`ois-passport` collects the records behind a release into an in-toto Statement with the OIS
passport predicate (specification section 8) and validates it before writing:

```bash
ois-passport build .ois/ ci-bundles/ \
  --release-name billing --release-version 2026.10.4 \
  --build "$BUILD" \
  --issuer https://ois.acme.example/actors/passport-issuer@1 \
  --approve-release-by https://ois.acme.example/actors/release-manager-1d2e \
  --output passport.json
```

- Inputs are record or bundle files, or directories such as `.ois/`, in JSON or YAML. Identical
  records are merged, a draft gives way to its approved record, and two different records with the
  same id are refused (R10).
- The passport lists the latest approved version of each intent unless `--intent` names them, and
  embeds every input record. Its subject is the build artifact with its digest.
- `--approve-release-by` adds the release decision for the build when the records carry none
  (R16). Exit code 1 means the passport would break a rule and nothing was written; warnings such as
  R12 are printed and the passport is written.
- The passport reports intent coverage by the reference method of Appendix D in `assessments`
  (below); `--no-coverage` leaves it out.
- The passport is not signed yet; DSSE signing follows.

## Measure intent coverage

Intent coverage is the share of criteria in scope with counted evidence: passed, derived from the
intent, for the released build or one of its inputs, not revoked, observed after its subject was
created and, for a criterion with a target, meeting it (Appendix D). `ois-coverage` shows per
criterion what counts and why other evidence does not:

```bash
ois-coverage passport.json                        # also checks the coverage the passport declares
ois-coverage .ois/ --build "$BUILD"               # before the passport: what is still missing
```

```
https://ois.acme.example/intents/INT-142@1#AC-1  not covered
    https://ois.acme.example/evidence/EV-9022: derivedFrom is unknown
https://ois.acme.example/intents/INT-142@1#AC-2  covered  https://ois.acme.example/evidence/EV-9021
intentCoverage 0.5 (declared 0.5)
```

| Measurement | Counts |
| --- | --- |
| `intentCoverage` | covered criteria in scope / criteria in scope |
| `intentCoverageAttested` | only evidence whose test context is attested (R12); 0 until signatures are verified |
| `intentCoverageWeighted` | criteria weighted by `criticality`: high 3, medium 2, low 1 |
| `intentCoverageDiverse` | only evidence whose test and subject come from a human, or from agents on different models |

A passport that lists several intents counts the criteria in scope of all of them together.
Exit code 1 means a declared value differs from the computed one, compared at the precision the
passport declares; 2 means a file could not be read or the release subject is missing.

## Privacy

None of the commands (`ois-validator`, `ois-junit`, `ois-intent`, `ois-record`, `ois-passport`,
`ois-coverage`) opens a network connection: the JSON Schema ships inside the package, and `ois-junit` and
`ois-record` read local files and the local Git checkout only. Installing the
package contacts PyPI. Details and a diagram:
[Privacy and data flows](https://openintentschema.io/privacy/).

## Documentation

- [Specification, schema and conformance fixtures](https://github.com/openintentschema/openintentschema)
- [Getting started](https://openintentschema.io/getting-started/): first OIS bundle from JUnit XML
- [Architecture](docs/architecture.md): how the validator and the commands are built, and why
- [Development](docs/development.md): setup, checks, specification sync, adding a rule, releases
- [Changelog](CHANGELOG.md)

## Contributing

What the validator accepts is decided in the specification repository; bug fixes, tooling and
documentation are welcome here. See [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md). Security issues: [SECURITY.md](SECURITY.md).

## License

Apache-2.0.
