# Architecture

How the validator and the JUnit adapter are built, and why. For setup, checks and releases see
[development.md](development.md).

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
| `cli.py` | `ois-validator`: exit 0 conforms, 1 violation, 2 unreadable file. |
| `junit.py`, `junit_cli.py` | `ois-junit`: JUnit XML to a Level 1 bundle (spec section 11.4). |
| `authoring.py` | Reads authored JSON, and YAML with the JSON schema of YAML 1.2 (section 7). |
| `intents.py`, `intent_cli.py` | `ois-intent`: seal, approve, next version, compare. |
| `passports.py`, `passport_cli.py` | `ois-passport build`: collect records, assemble and validate a passport. |
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

**R12 without signatures.** Signatures are not verified yet, so every recorded `context` is
reported as self-declared (warning), with the reason: no recorder, recorded by its own
producer, or no verified signature.

**Proposed rules.** R16 (release decision), R17 (digests), the rule ID `schema` and the
narrower R10 comparison (only intents ignore `status` and `digest`) are proposed in RFCs #1,
#2, #3 and #5. The fixtures already use them; if an RFC is rejected, the fixtures change and
this validator follows.

## No network access

None of the commands opens a network connection. The schema is bundled (`schemas/`) and loaded
through a local `referencing` registry, so no `$ref` is fetched; `ois-junit` runs local `git`
commands only (`rev-parse`, `ls-files`, `show`, `remote get-url`). This was verified by running
both commands and the whole test suite in a sandbox that blocks all network access. A new
dependency or feature must keep it that way; the specification's
[privacy page](https://openintentschema.io/privacy/) states it to users.

## Authoring intents

`ois-intent` exists because digests by hand are error-prone. Design choices:

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
- **Unsigned.** Signing wraps the finished statement in DSSE and is a separate step.

## JUnit adapter

`ois-junit` builds what the quick start of spec section 11.4 asks for and validates it before
writing.

- **Stable ids.** Criterion keys are `TC-` plus a hash of class and test name, valid for any
  test name and stable across runs. Artifact ids follow path and content, so a changed test
  file is a new artifact, as R10 requires.
- **Content at the commit.** Test files are found through `git ls-files` (the `file` attribute,
  or the longest prefix of the class name that names one file) and read with `git show` at
  `HEAD`, so `sha256` and `gitCommit` in one digest always agree. A test without a file is
  pinned by `gitCommit` alone, as section 6.4 allows.
- **Location** is `git+<origin>@<HEAD>`, credentials removed; scp-like remotes become `ssh` URIs.
- **XML** comes from the producer's own pipeline and is parsed with the standard library, whose
  Expat (2.4.1 and later) refuses entity expansion attacks and loads no external entities.
