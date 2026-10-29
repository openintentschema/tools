# Architecture

How `ois-tools` is built, and why. For setup, checks and releases see
[development.md](development.md).

## One command

`ois` is a thin dispatcher (`cli.py`): the first argument names the command, the rest goes to
that command's own `main(argv)` with its own argparse parser, whose `prog` is `ois <command>`.
Each command stays a module that can be called and tested on its own. Up to 0.5.0 they were
six separate console scripts in a package called `ois-validator`; one entry point is easier to
discover (`ois --help`) and leaves room for further commands.

## The validator implements the specification

What is valid is decided in the
[specification repository](https://github.com/openintentschema/openintentschema): the
specification text, the JSON Schema (Appendix B) and the conformance fixtures. Where the text
leaves a choice to the validator, `fixtures/README.md` there fixes it (scope of each rule,
reference resolution, levels). This repository copies schema, examples and fixtures at the
commit named in `spec.lock` and must pass every fixture exactly. If the validator and the
specification disagree, the specification wins; a gap in the specification is fixed there, by
RFC, not worked around here.

## Flow of one check

```
input JSON ──► schema.check ──► conforms? ──no──► reports (rule "schema")
                                    │
                                   yes
                                    ▼
                            Document.of(input)
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
             RECORD_RULES (R17)          CONTAINER_RULES (R1–R5, R8–R16)
             any input                   bundles and passports only
                    └───────────────┬───────────────┘
                                    ▼
                         Result(reports: list[Report])
```

`validate()` in `validator.py` runs the schema first. The integrity rules run only on input
that passed it, so they can rely on its structure.

## Modules

| Module | Responsibility |
| --- | --- |
| `report.py` | `Level` (violation, warning, info; spec section 11.1), `Report` (level, rule, message, JSONPath) and `Result`. `conforms` is false only for violations. |
| `schema.py` | Appendix B check of a record, a bundle or a passport statement. |
| `document.py` | `Document`: the records of the input with their JSONPaths, reference resolution, every Ref-typed field. |
| `rules.py` | One function per integrity rule, in rule order, registered in `RECORD_RULES` or `CONTAINER_RULES`. |
| `digests.py` | Criterion and intent `sha256` over RFC 8785 (JCS), digest-set equality. |
| `validator.py` | `validate()`: schema, then rules. |
| `cli.py` | `ois`: the entry point; passes the arguments after the command name to that command's `main`. |
| `validate_cli.py` | `ois validate`: exit 0 conforms, 1 violation, 2 unreadable file. |
| `sarif.py` | SARIF 2.1.0 output of `ois validate`. |
| `cucumber.py`, `cucumber_cli.py` | Gherkin scenarios as criteria (`ois intent from-gherkin`) and Cucumber Messages as evidence (`ois cucumber`). |
| `profiles.py` | Known profiles (`regulated/v0.1`) and their rules, applied when listed or asked for. |
| `keyless.py` | Sigstore bundles: offline verification against trusted roots, certificate identity. |
| `trust_cli.py` | `ois trust update`: a Sigstore trusted root through TUF, the only command with network access. |
| `signing.py`, `sign_cli.py`, `key_cli.py` | DSSE envelopes: keys and key ids, signing, verification under trusted keys; `ois sign`, `ois key`. |
| `formats.py` | Test reports by content: JUnit XML, TRX, Playwright JSON, Jest and Vitest JSON, `go test -json`. |
| `measures.py`, `measure_cli.py` | `ois measure`: k6 summaries and JMeter results as measures against criterion targets. |
| `junit.py`, `junit_cli.py` | `ois junit`: test reports to a Level 1 bundle (spec section 11.4), or with `--intent` to evidence for an approved intent. |
| `authoring.py` | Reads authored JSON, and YAML with the JSON schema of YAML 1.2 (section 7); walks input directories. |
| `intents.py`, `intent_cli.py` | `ois intent`: seal, approve, next version, compare. |
| `passports.py`, `passport_cli.py` | `ois passport build`: collect records, assemble and validate a passport. |
| `coverage.py`, `coverage_cli.py` | Intent coverage by the reference method of Appendix D and its variants; `ois coverage`. |
| `records.py`, `record_cli.py` | `ois record`: artifacts, actors, evidence, decisions, findings, finding chains and bundles. |
| `schemas/ois-0.1.schema.json` | Copy of Appendix B, shipped in the package. |

## Design decisions

**Each record against its own type.** The schema's root is `oneOf` record or bundle, so a
broken record inside a bundle surfaces as one bare `oneOf` error. `schema.py` therefore checks
each record against `#/$defs/<type>` and the bundle or passport predicate only for its own
members. The report points at the failing field, for example
`$.records[7].criteria[0].notation`.

**No false "unexpected" fields.** Under JSON Schema 2020-12, when an `allOf` branch fails, its
properties count as not evaluated, so `unevaluatedProperties` also flags valid common fields
such as `ois` and `id`. `schema.py` reports only names the definition does not declare.

**Passports.** A passport is an in-toto Statement v1, recognised by `_type`. Appendix B covers its
`predicate` only, so the statement's `subject` digests are read defensively for R16.

**Reference resolution.** `<id>@<version>` resolves to the intent or actor with that id and
version; `<id>` to a record with that id and no version; `<id>@<version>#<key>` to the intent
version, the key being R2's concern. A plain reference to an agent therefore does not resolve.

**No cascades.** A rule that needs the record behind an unresolved reference skips it; R10
reports the reference once. A reference may resolve to a record of another type than expected,
so rules check a resolved record's `type` before reading type-specific fields. A test points
every reference of every valid fixture at every other record to make sure no rule crashes.

**Rule scope.** R17 also applies to a single record. All other rules relate records to each
other and run on bundles and passports only; R13, R15 and R16 only on passports.

**R12 and signatures.** A recorded `context` is attested when the document's envelope carries a
verified signature by the recorder (see Signatures). Otherwise it is reported as self-declared
(warning), with the reason: no recorder, recorded by its own producer, or no verified signature
by the recorder.

**Rules of draft 5.** R16 (release decision), R17 (digests), the rule IDs `schema` and
`signature` and the narrower R10 comparison (only intents ignore `status` and digests) came with
draft 5 of the specification.

## No network access

No command opens a network connection, with one explicit exception: `ois trust update` fetches a
Sigstore trusted root through TUF when it is run, so that verifiers need not use another tool;
verification itself reads that file. The schema is bundled (`schemas/`) and loaded through a local
`referencing` registry, so no `$ref` is fetched; `ois junit`, `ois cucumber` and `ois record` run
local `git` commands only (`rev-parse`, `ls-files`, `show`, `diff`, `remote get-url`). This was
verified by running every command and the whole test suite in a sandbox that blocks all network
access, and the container image with `docker run --network none` in CI. A new dependency or feature
must keep it that way, and a command that needs the network must say so in its name or help, as `ois
trust update` does; the specification's [privacy page](https://openintentschema.io/privacy/) states
it to users.

## Authoring intents

`ois intent` exists because digests by hand are error-prone. Design choices:

- **YAML without surprises.** PyYAML reads YAML 1.1, where `yes` is a boolean, `0123` an octal
  number and `2026-10-14T09:12:00Z` a datetime. `authoring.py` replaces its implicit resolvers
  with the JSON schema of YAML 1.2 and refuses tags, merge keys, non-string and duplicate keys,
  because section 7 requires a lossless conversion to JSON and the digest covers the result.
- **Immutability is enforced.** `approve` recomputes the digest of an already approved intent and
  refuses it if the content changed; the way forward is `next`.
- **Every output is validated** (schema and R17) before it is written. The decision's
  `subjectDigest` is copied from the sealed intent, so R1 holds by construction.
- **Repository layout.** Files go to `.ois/intents/` and `.ois/decisions/` as section 7
  recommends; drafts stay YAML, approved records are JSON.

## Assembling passports

- **One record per id.** `collect` merges identical copies from several inputs (the same actor in
  every bundle is normal), keeps the approved state of an intent and refuses different records
  with the same id, using the same comparison as R10 (`rules.content`).
- **Defaults follow the rules.** Without `--intent`, each intent is listed in its latest approved
  version (R15); the subject is the build artifact's digest, so R16 can bind the release decision
  to it.
- **Nothing non-conforming leaves.** The statement is validated before it is returned; a violation
  raises, warnings are left to the caller to show.
- **Coverage by default.** `assessments` carries the four measurements of Appendix D, computed
  from the passport's own records, intents and scope; `--no-coverage` leaves them out for producers
  who report their own method.
- **Signed as built.** `--sign` wraps the finished statement in DSSE. The attested coverage is
  part of the signed content, so it is computed with the key ids of exactly these keys as
  signers; a signature added later cannot change it.

## Container image

The `Dockerfile` builds the wheel from source in one stage and installs it in a slim Python image
with the runtime dependencies exported from `uv.lock` and checked by hash, so the image holds
exactly what the lock file names. git is installed because `ois junit`, `ois cucumber` and `ois
record` read the checkout; `safe.directory '*'` lets git read a checkout mounted from the host,
which belongs to another user. The image runs as an unprivileged user with `HOME=/tmp`, so `docker
run --user` with the host's ids works and written files belong to the host user. Its entrypoint is
`ois`, so the arguments after the image name are those of the command line; CI systems that run a
script in the image override it (GitLab: `entrypoint: [""]`).

## Signatures

`signing.py` implements DSSE (section 8.3) with `cryptography`; the specification repository's
`fixtures/README.md` fixes the reading, and its signed fixtures were generated independently.

- **Envelope first.** `validate()` opens an envelope before anything else: the signature reports,
  then the usual checks on the payload with the set of key ids whose signatures verified
  (`Document.signers`). An envelope with a `signature` violation passes an empty set, so it
  counts as unsigned and R12 reports its contexts.
- **Key ids** are the hex SHA-256 of the DER SubjectPublicKeyInfo, so `--trusted-key file.pub`
  needs no separate key id; `KEYID=FILE` covers keys with other ids, such as KMS aliases.
- **Trust comes from the command line only.** Keys in a document are never trusted; otherwise a
  producer could attest its own contexts.
- **Attribution** follows section 8.3: a signer key id counts for the actor whose
  `identity.keyId` equals it; certificate identities (`issuer`, `subject`) come from Sigstore
  bundles (see Keyless signatures).
- **Algorithms:** Ed25519 (`ois key generate`) and ECDSA P-256 with SHA-256, the usual choices
  for DSSE and Sigstore. Ed25519 signs deterministically, which the fixtures rely on.
- **Signing validates first.** `ois sign` refuses a document with a violation, so nothing broken
  gets a signature; adding a signature keeps the payload bytes unchanged.

## SARIF

`sarif.py` maps reports to SARIF 2.1.0: violation to `error`, warning to `warning`, info to
`note`, each rule with a link to its explanation. A small JSON scanner turns a report's JSONPath
into the line of that value, or of its nearest existing parent; reports on an envelope's payload
point at the `payload` line. The JSONPath is kept as the logical location.

## Gherkin and Cucumber

`cucumber.py` reads feature files with `gherkin-official` and runs as Cucumber Messages (NDJSON),
whose `gherkinDocument` has the same structure, so one renderer gives the criterion statement
for both: background steps, scenario steps, data tables, doc strings and outline examples.

- **The key is a tag**, matched by `--key-pattern` (default `KEY-1` style), so it stays stable
  across versions while scenario names change.
- **Derived from the intent** only when the rendered scenario equals the approved criterion's
  statement. That comparison is what `ois cucumber` observes, so it is `contextRecordedBy` of the
  test artifact and its context lists only those criteria (R4, R12).
- **Final attempts only:** a test case finished with `willBeRetried` is ignored. A scenario's
  result is its worst step: failed, then undefined, ambiguous or unknown (`error`), then skipped
  or pending.
- **Records go to `.ois/`** through `records.store`, as with `ois record`; evidence ids hash the
  test, key, row and time, so the rows of an outline stay apart.
- Messages are read for structure and status only; error messages and stack traces are not
  copied.

## Measures

`ois measure` gives non-functional criteria the measure that Appendix D needs to count them.

- **One metric syntax**, `METRIC{TAG:VALUE}:STAT`, taken from k6's submetric names, so JMeter
  results are filtered the same way (`elapsed{label:Checkout}:p(95)`).
- **The measure is named after the target's `metric`**: the intent decides the name, the
  command line only maps a value of the run to it. `result` is the comparison with the target.
- **Units** are known for k6 time metrics (`contains: time`, or the built-in `http_req_*`) and
  JMeter time columns (`ms`). A unit that differs from the target's is refused rather than
  converted.
- **Percentiles** of JMeter samples are interpolated between closest ranks, as k6 does, so both
  tools give the same value for the same samples.

## CI building blocks

- **GitHub:** `.github/actions/setup` is a composite action: `setup-uv`, then
  `uv tool install ois-tools[sigstore]==<version>`, with `ois` on the `PATH`. Its `version`
  default is the release it belongs to, so `uses: …/setup@v<version>` installs that version; `source`
  installs a checkout instead, which the CI job `Setup action` uses.
- **GitLab:** `.gitlab/ci/ois.gitlab-ci.yml` defines hidden jobs on the image of the same
  release, with the entrypoint cleared so GitLab can run the script.
- **One version.** `tests/test_ci_templates.py` fails unless `pyproject.toml`, the action's
  default and the template's image tag agree.
- **Placement.** Both live in the hosts' own folders, `.github/` and `.gitlab/`, which keeps the
  root lean; an action outside the root cannot be listed in the GitHub Marketplace.

## Profiles

`profiles.py` checks the profiles it knows after the core rules, on the same `Document`. A
profile applies when a record of the document lists its URI or the verifier names it, and then to
the whole document, as `fixtures/README.md` reads section 10.2. Rules a document can meet only
once signed (`BEFORE_SIGNING`, P3) are left out where a document is checked before signing:
`passports.build` and `ois sign`. `Document.enveloped` tells whether the input came in a signed
envelope; whether the signature verifies is the `signature` rule's concern.

## Keyless signatures

`keyless.py` reads Sigstore bundles with the optional `sigstore` package, verifying offline:

- **No provider code.** A provider is a trusted root and an OIDC issuer; `--trusted-root` takes
  any, including the JSON lines of `gh attestation trusted-root`. Organizations with their own
  Sigstore instance pass its trusted root.
- **Identity after verification.** Certificate chain, transparency log entry and signature are
  checked with `Verifier.verify_dsse` and a policy that accepts any identity but records it; the
  identity (OIDC issuer extension, subject alternative name) is then matched to actors by R12, as
  the attribution of section 8.3 requires. Matching is exact; a GitHub workflow's subject includes
  its ref.
- **Same payload rules as DSSE.** The bundle's DSSE envelope goes through `signing.read_payload`,
  so payload type and content are checked alike; `Signers` carries key ids and certificate
  identities together.
- **Optional.** Without the package, `--trusted-root` fails with an install hint; a bundle without
  trusted roots validates as unsigned with a `signature` warning.
- **Tests** use the real provenance bundle of the ois-tools 0.6.0 image and a frozen public-good
  trusted root, so they run offline.

## Intent coverage

`coverage.py` implements the reference method of Appendix D, which is informative: no rule and no
fixture requires it, and `ois validate` does not report on declared coverage. `ois coverage`
compares instead.

- **One function per condition.** For each criterion in scope, every evidence record for the same
  intent and key is either counted or given the first condition it fails, in the order of
  Appendix D. That reason is what `ois coverage` prints, so a missing figure can be traced.
- **Stale evidence** is read as R4 reads context: evidence for an earlier version counts if that
  version is among the records and its criterion with the same key has the same digest
  (section 6.3). Evidence for a later version never counts.
- **Several intents.** Appendix D defines coverage for one intent version; a passport lists
  several. The criteria in scope of all listed intents are counted together, as Appendix D says
  since draft 5.
- **The release subject** of a passport is the build of its approved or passed release decision
  whose digest matches the statement subject, as R16 finds it.
- **Attested** uses `rules.self_declared`, the reason R12 reports, with the key ids that signed
  the passport under trusted keys.
- **Diverse** follows the text literally: a human producer counts, two agents count if no model of
  one shares provider and name with a model of the other, and a tool does not count.
- **Values** are rounded to four decimals. A declared value agrees if it is equal at its own
  precision (0.33 agrees with 0.3333).

## Recording the other records

`ois record` closes the gap between `ois intent` and `ois passport`: without it, artifacts,
actors, decisions and findings were written by hand. Design choices:

- **One record per command, its reference on stdout.** A pipeline captures it
  (`BUILD=$(ois record artifact …)`) and passes it to the next command; what was written goes to
  stderr. Records go to `.ois/<type>s/`, next to what `ois intent` writes, so `ois passport build
  .ois/` finds them.
- **Ids that do not depend on the moment.** Actors are named (`actors/ci-build`, versioned by
  `--version`); artifacts take a readable name and the start of their digest; evidence, decisions
  and findings a hash of their content without `id` and `createdAt`. Recording the same thing twice
  therefore lands on the same file, which is kept: records never change, and a different record
  under an existing id is refused, as R10 would.
- **Git is the default location.** A file without `--location` must be committed and unchanged
  (`ls-files --error-unmatch`, `diff --quiet HEAD`), so that `sha256`, `gitCommit` and
  `git+<origin>@<HEAD>` describe the same content. The location reuses `junit.location_of`, which
  strips credentials.
- **Rules where the records at hand allow them.** Each record passes the schema and R17 before it
  is written. `decision` resolves its subject among the output directory and `--records` as
  validators do, copies the subject's digest (R1, R16) and checks the well-known gates of section
  4.6. For `finding-resolution` and `supersede` it applies R14: one successor per finding and a
  confirming actor who did not produce, record or decide any part of the resolution. Everything
  across records that this cannot see is left to `ois validate` and `ois passport`.
- **Bundles are self-contained.** `bundle` reuses `passports.collect` and refuses a bundle with a
  violation, typically an unresolved reference (R10).

## JUnit adapter

`ois junit` builds what the quick start of spec section 11.4 asks for and validates it before
writing.

- **Stable ids.** Criterion keys are `TC-` plus a hash of class and test name, valid for any
  test name and stable across runs. Artifact ids follow path and content, so a changed test
  file is a new artifact, as R10 requires.
- **Content at the commit.** Test files are found through `git ls-files`: the report's file
  path, also absolute or from another checkout (matched by its tracked suffix); a class name
  that is a path; for Go, the `_test.go` file in the package directory (from `go.mod`) that
  declares the test function; or the longest prefix, or the last part, of the class name that
  names one file. They are read with `git show` at `HEAD`, so `sha256` and `gitCommit` in one
  digest always agree. A test without a file is pinned by `gitCommit` alone, as section 6.4
  allows.
- **Formats** are recognized by content, so one command serves every runner. The native
  reports are read where JUnit XML loses data: tags, categories, per-test times and the final
  attempt of a retried test (Playwright). Only names, results, times, tags and paths are read.
- **Location** is `git+<origin>@<HEAD>`, credentials removed; scp-like remotes become `ssh` URIs.
- **XML** (JUnit, TRX) comes from the producer's own pipeline and is parsed with the standard library, whose
  Expat (2.4.1 and later) refuses entity expansion attacks and loads no external entities.

With `--intent`, `record_run` writes evidence for an approved intent instead (Level 2).

- **Keys** come from `criterion` properties, tags and test name. These are the places that reach
  reports in common runners; JUnit `@Tag`, xUnit `[Trait]` and Playwright tags in JUnit XML do
  not. Name matches allow `_` around the key for Go subtests.
- **derivedFrom** is claimed as `intent` and kept only if `rules.derivation_problems` (R4, R5)
  finds nothing, so the adapter and the validator cannot disagree.
- **Known test artifacts are reused**, matched by path and `sha256`: a context recorded by an
  agent runtime stays the one that counts, and a second artifact under the same id is avoided.
  `--written-from-intent` only adds a self-declared context to artifacts the adapter creates; the
  adapter cannot observe how a test was written, so it is not `contextRecordedBy`.
