# ois-tools

[![PyPI](https://img.shields.io/pypi/v/ois-tools)](https://pypi.org/project/ois-tools/)
[![Checks](https://github.com/openintentschema/tools/actions/workflows/checks.yml/badge.svg)](https://github.com/openintentschema/tools/actions/workflows/checks.yml)

Reference tools for the [Open Intent Schema (OIS)](https://github.com/openintentschema/openintentschema),
the open data standard that links what was required to what was built and to the evidence in
between. One command, `ois`, records that evidence along the delivery lifecycle and checks it:

| Phase | What is recorded | Command |
| --- | --- | --- |
| Requirement | intent with acceptance criteria, its approval | `ois intent`, `ois intent from-gherkin` |
| Design | design approval | `ois record decision --gate design-approval` |
| Implementation | code artifact, the context an agent was given | `ois record artifact` |
| Test | evidence per criterion, measures for non-functional ones | `ois junit --intent`, `ois cucumber`, `ois measure`, `ois record evidence` |
| Merge | merge decision | `ois record decision --gate merge` |
| Release | build, release decision, signed passport with intent coverage | `ois record`, `ois passport build --sign` |
| Operation | findings, their classification and resolution | `ois record finding`, `ois record supersede` |
| Review by others | check a passport and recompute its coverage | `ois validate`, `ois coverage` |

Along the way, `ois key`, `ois sign` and `ois trust` sign and verify: DSSE with your own keys, or
keylessly through Sigstore, for example with GitHub Actions (below).

The tools record and check; they do not enforce. Whether a merge or a release is blocked, for
example below a coverage threshold or with an open critical finding, is the policy of the
project's pipeline, which reads the commands' exit codes and figures.

`ois validate` checks OIS records, bundles and Quality Passports and reports every problem with
its rule and level (specification section 11.1):

- **violation:** a MUST is broken; the input does not conform.
- **warning:** R8, R12 and R13 ask for a report; the input still conforms.
- **info:** a SHOULD is not followed.

**Status:** pre-alpha, targets specification v0.1. `ois validate` checks Appendix B and every rule
of section 5 that can be checked from the records at hand (R1–R5, R8–R17) and passes all
conformance fixtures, signed ones included. Signatures are verified with keys you trust (Ed25519
or ECDSA P-256) or, for keyless Sigstore signatures, against the trusted roots you choose. Up to
version 0.5.0 the package was called `ois-validator`, with one command per tool
(`ois-validator`, `ois-junit`, …).

## Install

```bash
uvx --from ois-tools ois validate bundle.json    # run without installing
pip install ois-tools                            # or install; Python 3.13 or later
ois --help                                       # the commands
```

The commands work for projects in any language: they read JSON, YAML, test reports and the local
Git checkout, not the code. Without Python, use [uv](https://docs.astral.sh/uv/) alone (`uvx`
fetches a Python on first use) or the container image, which holds `ois` and git:

```bash
docker run --rm --network none -v "$PWD:/work" --user "$(id -u):$(id -g)" \
  ghcr.io/openintentschema/ois-tools validate bundle.json
```

- The image runs `ois`; its arguments follow the image name.
- `-v "$PWD:/work"` mounts the current directory; paths are relative to it.
- `--user` writes files as you rather than as the image's user.
- `--network none` is optional and shows that the commands need no network (`ois trust update`
  aside, which fetches a trusted root).
- In a CI job that uses the image as its image, override the entrypoint (GitLab:
  `entrypoint: [""]`) and call `ois` in the script.
- Tags: `0.11.0`, `0.11` and `latest`. Each image carries a signed build provenance and an SBOM:
  `gh attestation verify oci://ghcr.io/openintentschema/ois-tools:0.11.0 --owner openintentschema`.

## Use in CI

**GitHub Actions:** the action `.github/actions/setup` installs `ois` in the version of its tag,
Sigstore verification included:

```yaml
- uses: openintentschema/tools/.github/actions/setup@v0.11.0   # pin the tag's commit SHA in production
- run: ois junit build/test-results/test/*.xml --namespace https://ois.acme.example --suite "Team Billing" --output ois-bundle.json
```

**GitLab CI:** include the template of a release and extend `.ois-junit`, or `.ois` for any
command:

```yaml
include:
  - remote: https://raw.githubusercontent.com/openintentschema/tools/v0.11.0/.gitlab/ci/ois.gitlab-ci.yml

ois:
  extends: .ois-junit
  needs: [test]
  variables:
    OIS_JUNIT_REPORTS: build/test-results/test/*.xml
    OIS_NAMESPACE: https://ois.acme.example
    OIS_SUITE: Team Billing
```

**Any other CI** runs the container image or `uvx --from ois-tools ois …`.

**GitHub code scanning** shows the reports at their lines, from SARIF:

```yaml
- run: ois validate passport.json --format sarif > ois.sarif
- uses: github/codeql-action/upload-sarif@9b3254085494b55a0f7c4c2b3d2d52cc712ce370 # v4.39.1
  if: ${{ !cancelled() }}
  with:
    sarif_file: ois.sarif
    category: ois
```

The job needs `permissions: security-events: write`; code scanning is free for public
repositories and needs GitHub Advanced Security for private ones.

## Validate

```bash
ois validate bundle.json passport.json
ois validate --format json finding.json
cat bundle.json | ois validate -
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
from ois_tools import validate

result = validate(document)  # a parsed JSON document
result.conforms  # False if any report is a violation
result.reports  # list of Report(level, rule, message, path)
```

## Record test results

`ois junit` turns a test report into a bundle, as the Level 1 quick start in specification
section 11.4 describes. It reads JUnit XML, which almost every test framework writes, and
recognizes by content the native reports of Playwright (JSON), Jest and Vitest (JSON), Go
(`go test -json`) and .NET (TRX). Run it in the Git checkout the tests ran in:

```bash
ois junit build/test-results/*.xml \
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
`gitCommit`) and an evidence record per result with `derivedFrom: unknown`. `ois junit`
validates the bundle before writing it: exit code 0 writes it, 1 means it would not conform and
nothing is written, 2 means a report or the Git checkout could not be read.

The [getting started guide](https://openintentschema.io/getting-started/)
walks through it step by step, including a CI example.

### Evidence for an approved intent

Tests that name a criterion key such as `AC-1` give evidence for that criterion of an approved
intent, from any of these reports:

```bash
ois junit build/test-results/*.xml --namespace https://ois.acme.example \
  --intent https://ois.acme.example/intents/INT-7@1 --test-producer "$QE" \
  --subject "$BUILD" --written-from-intent
```

- **Keys** come from a `criterion` property or annotation (pytest `user_properties`,
  Playwright), from tags and test categories (Playwright and Vitest JSON, TRX), or from the test
  name between non-alphanumeric characters (`@DisplayName`, Jest, Vitest, Go subtests);
  `--key-pattern` changes the pattern. Tests without a key are counted and skipped.
- **Records** go to `.ois/`: evidence per test and key, and a test artifact per file. A test
  artifact already recorded there or in `--records` for the same file content is reused, so an
  agent runtime's context counts.
- **`derivedFrom: intent`** where R4 and R5 allow it, otherwise `unknown`. `--written-from-intent`
  declares the named criteria as the context of new test artifacts: self-declared (R12), so it
  counts in `intentCoverage` but not in `intentCoverageAttested`.

## Write and approve intents

`ois intent` takes an intent written in YAML or JSON and does what is error-prone by hand:

```bash
ois intent seal INT-142.ois.yaml                      # criterion and intent digests (RFC 8785)
ois intent approve INT-142.ois.yaml --by https://ois.acme.example/actors/po-7f3a
ois intent next .ois/intents/INT-142-v1.ois.json      # draft of version 2, superseding version 1
ois intent compare .ois/intents/INT-142-v1.ois.json .ois/intents/INT-142-v2.ois.yaml
```

- `approve` writes the approved intent and its intent-approval decision, bound by the intent's
  digest (R1), to `.ois/intents/` and `.ois/decisions/` (specification section 7). It refuses an
  approved intent whose content no longer matches its digest: approved intents never change.
- `compare` shows per criterion whether evidence carries over to the new version or is stale
  (section 6.3); `criticality` does not count.
- YAML is read as section 7 requires: `2026-10-14T09:12:00Z` and `yes` stay strings, and tags,
  merge keys and non-string or duplicate keys are refused, so that digests do not depend on how
  a YAML library reads the text.

## Gherkin and Cucumber

Feature files become the intent, and a Cucumber run its evidence. A scenario names its criterion
with a key tag such as `@AC-1`:

```bash
ois intent from-gherkin features/rounding.feature --id https://ois.acme.example/intents/INT-7
ois intent approve .ois/intents/INT-7-v1.ois.yaml --by "$PO"

npx cucumber-js --format message:cucumber.ndjson          # Cucumber Messages; also cucumber-jvm, Reqnroll
ois cucumber cucumber.ndjson --intent https://ois.acme.example/intents/INT-7@1 \
  --test-producer "$PO" --subject "$CODE"
```

- **Criteria:** one per scenario with a key tag (`--key-pattern` to change it), statement in
  `gherkin` notation: background and steps, and the examples of an outline. Scenarios without a
  key tag are skipped.
- **Evidence:** one per executed scenario, written to `.ois/` with a test artifact per feature
  file. Results: passed, failed, skipped or pending, and `error` for undefined steps.
- **`derivedFrom: intent`** only where the scenario is worded exactly as its approved criterion;
  `ois cucumber` checks this and records itself as `contextRecordedBy`. A scenario that differs
  is recorded with `derivedFrom: unknown`.
- Step definitions are code written for the scenarios; the independence of R5 covers the
  scenario text, not them.
- Error messages and stack traces from the run are not copied.

## Measure non-functional criteria

`ois measure` records a load test run against the `target` of a non-functional criterion:

```bash
k6 run --summary-export=summary.json load/checkout.js       # or JMeter: jmeter -n -t checkout.jmx -l results.jtl
ois measure summary.json --intent https://ois.acme.example/intents/INT-9@1 \
  --criterion "AC-3=http_req_duration:p(95)" --test load/checkout.js --test-producer "$QE" \
  --subject "$BUILD" --written-from-intent
```

- **Results:** k6 summaries (`--summary-export` or `handleSummary` JSON) and JMeter JTL CSV,
  recognized by content.
- **`--criterion KEY=METRIC:STAT`**, repeatable: a k6 metric or submetric
  (`http_req_duration{expected_response:true}`), or for JMeter a column (`elapsed`, `Latency`,
  `Connect`, …), `samples` or `errors`, filtered with `{label:NAME}`; statistics `avg`, `min`,
  `med`, `max`, `p(N)`, `count` and `rate`.
- **Evidence** per criterion, with the value as the measure the target names (`ms` for times),
  `pass` if it meets the target, otherwise `fail`. A criterion without target, or a unit that
  differs from the target's, is refused. The test artifact and `derivedFrom` work as with
  `ois junit --intent`.

## Record artifacts, actors, evidence, decisions and findings

`ois record` writes every other record type, one record per command, to `.ois/<type>s/`. It
computes what is error-prone by hand, validates the record and prints the reference other records
use for it, so a pipeline can pass it on:

```bash
export OIS_NAMESPACE=https://ois.acme.example              # for derived ids, or --namespace

CI=$(ois record actor --kind tool --name "CI build" --version 7)
DEV=$(ois record actor --kind agent --name "Dev Agent" --version 2.1 --model acme-ai:coder:2026-09)
CODE=$(ois record artifact src/invoice.py --kind code --produced-by "$DEV")
BUILD=$(ois record artifact dist/billing-2026.10.4-py3-none-any.whl --kind build \
  --location pkg:pypi/billing@2026.10.4 --produced-by "$CI" --input "$CODE")
ois record decision --gate merge --subject "$CODE" --outcome passed --by "$CI"
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

`ois passport` collects the records behind a release into an in-toto Statement with the OIS
passport predicate (specification section 8) and validates it before writing:

```bash
ois passport build .ois/ ci-bundles/ \
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
- `--sign KEY` signs the passport with DSSE as it is built (below); the attested coverage inside
  it then counts these keys.

## Measure intent coverage

Intent coverage is the share of criteria in scope with counted evidence: passed, derived from the
intent, for the released build or one of its inputs, not revoked, observed after its subject was
created and, for a criterion with a target, meeting it (Appendix D). `ois coverage` shows per
criterion what counts and why other evidence does not:

```bash
ois coverage passport.json                        # also checks the coverage the passport declares
ois coverage .ois/ --build "$BUILD"               # before the passport: what is still missing
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
| `intentCoverageAttested` | only evidence whose test context is attested: recorded by another actor who signed the passport (R12) |
| `intentCoverageWeighted` | criteria weighted by `criticality`: high 3, medium 2, low 1 |
| `intentCoverageDiverse` | only evidence whose test and subject come from a human, or from agents on different models |

`--format markdown` writes tables for a CI job summary or a pull request comment, for example
`>> "$GITHUB_STEP_SUMMARY"`; ids are shortened to their last part.

A passport that lists several intents counts the criteria in scope of all of them together.
Exit code 1 means a declared value differs from the computed one, compared at the precision the
passport declares; 2 means a file could not be read or the release subject is missing.

## Profiles

A profile adds rules for an industry (specification section 10.2). `ois validate` checks the
profiles it knows when a record lists them in `profiles`, or when asked:

```bash
ois validate passport.dsse.json --profile regulated/v0.1 --trusted-key keys/
```

| Rule | Violation |
| --- | --- |
| `regulated/P1` | an intent without `risk` |
| `regulated/P2` | an intent approval or release decided by an agent or tool |
| `regulated/P3` | a passport that is not signed |
| `regulated/P4` | a production dataset as test input |

P3 holds only once a passport is signed, so `ois passport build` and `ois sign` do not refuse an
unsigned passport for it; `ois validate` reports it until it is signed.

## Sign bundles and passports

Signing makes records attributable (specification section 8.3, Level 3). A signature counts for an
actor whose `identity.keyId` is the key's id, and only for a verifier that trusts the key:

```bash
ois key generate agent-runtime            # writes agent-runtime.key (secret) and .pub, prints the key id
ois record actor --kind tool --name "Agent runtime" --version 4.3 --public-key agent-runtime.pub

ois sign bundle.json --key agent-runtime.key --output bundle.dsse.json   # or add to an envelope
ois passport build .ois/ … --sign issuer.key --sign agent-runtime.key --output passport.dsse.json

ois validate passport.dsse.json --trusted-key keys/       # a .pub file, a directory, or KEYID=FILE
```

- A **key id** is the hex SHA-256 of the public key (DER SubjectPublicKeyInfo), so a verifier
  derives it from the key it trusts. `ois key id FILE` prints it.
- **Trust** is the verifier's decision: only `--trusted-key` counts, never a key a document names.
- **R12:** a recorded context is attested when its `contextRecordedBy` actor signed the document.
  The runtime that ran an agent therefore signs the bundle or passport next to the producer;
  a DSSE envelope holds several signatures.
- **Reports** under the rule ID `signature` (proposed): a violation for an envelope without
  signatures, with the wrong payload type, or with a signature that does not verify, and the
  envelope then counts as unsigned; a warning for a key that is not trusted.
- Private keys are written readable by you only and are never needed for verification. Keep them
  in a secret store; an encrypted key is opened with `OIS_KEY_PASSPHRASE`.

### Keyless with Sigstore, for example on GitHub

In a pipeline, no key needs to be kept: GitHub Actions (or GitLab CI, Google Cloud, Buildkite)
proves the workflow's identity to Sigstore, which certifies it for ten minutes and logs the
signature publicly. On GitHub, `actions/attest` signs the passport:

```bash
ois passport build .ois/ … --keyless-signer \
  "https://token.actions.githubusercontent.com=$GITHUB_SERVER_URL/$GITHUB_WORKFLOW_REF" \
  --output passport.json                      # attested coverage counts the workflow
jq .predicate passport.json > predicate.json  # actions/attest builds the statement itself
```

```yaml
- uses: actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6 # v4.2.2
  with:
    subject-name: billing
    subject-digest: sha256:${{ steps.passport.outputs.digest }}   # the passport's subject
    predicate-type: https://ois.example/passport/v0.1
    predicate-path: predicate.json
```

```bash
pip install 'ois-tools[sigstore]'
ois trust update --output trusted_root.json                # through TUF; refresh it now and then
# or, from the GitHub CLI, with GitHub's own instance too: gh attestation trusted-root > trusted_root.jsonl
ois validate passport.sigstore.json --trusted-root trusted_root.json
```

- The signer is the certificate identity. An actor that signs keylessly names it:
  `ois record actor … --identity-issuer https://token.actions.githubusercontent.com
  --identity-subject https://github.com/<owner>/<repo>/.github/workflows/<file>@<ref>`.
- Another provider or an organization's own Sigstore instance is another trusted root and another
  issuer; nothing in the tools is specific to GitHub. Outside GitHub, `cosign attest-blob` signs.
- Verification runs offline; the trusted root changes when Sigstore rotates keys, so refresh it.
- People should not sign through the public instance, which puts their e-mail address into the
  certificate (specification section 12): let them approve in GitHub (for example as required
  reviewers of an environment) and the workflow sign, or give them their own key.

## Privacy

No command opens a network connection except `ois trust update`, which fetches a Sigstore trusted
root when you run it. The JSON Schema ships inside the package, `ois junit`, `ois cucumber` and `ois
record` read local files and the local Git checkout only, and verifying signatures uses local files.
Installing the package contacts PyPI, pulling the container image contacts GitHub (ghcr.io), and
`uvx` without a local Python downloads one from GitHub. Details and a diagram: [Privacy and data
flows](https://openintentschema.io/privacy/).

## Documentation

- [Specification, schema and conformance fixtures](https://github.com/openintentschema/openintentschema)
- [Getting started](https://openintentschema.io/getting-started/): first OIS bundle from a test report
- [Architecture](docs/architecture.md): how the commands are built, and why
- [Development](docs/development.md): setup, checks, specification sync, adding a rule, releases
- [Changelog](CHANGELOG.md)

## Contributing

What `ois validate` accepts is decided in the specification repository; bug fixes, tooling and
documentation are welcome here. See [CONTRIBUTING.md](.github/CONTRIBUTING.md) and the
[Code of Conduct](.github/CODE_OF_CONDUCT.md). Security issues: [SECURITY.md](.github/SECURITY.md).

## License

Apache-2.0.
