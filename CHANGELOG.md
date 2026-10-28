# Changelog

All notable changes to `ois-validator`. Versions follow [Semantic Versioning](https://semver.org/);
during 0.x any release may change behaviour.

## Unreleased

## 0.5.0 (2026-10-28)

- Intent coverage by the reference method of Appendix D: `intentCoverage` and the variants
  `intentCoverageAttested`, `intentCoverageWeighted` and `intentCoverageDiverse`
  (`ois_validator.coverage`).
- `ois-passport build` records them in the passport's `assessments`; `--no-coverage` leaves them
  out. Signatures are not verified yet, so `intentCoverageAttested` is 0 wherever evidence rests
  on a context (R12).
- `ois-coverage`: per criterion, what counts and why other evidence does not; for a passport, a
  check of the coverage it declares (exit code 1 if it differs).
- Container image `ghcr.io/openintentschema/ois-validator` with every command and git, for
  projects in any language (linux/amd64 and linux/arm64, with build provenance and SBOM).
  Published by the release workflow after the PyPI release.
- `ois-record artifact --commit` names the artifact after the origin repository instead of the
  checkout's folder, which differs between clones and containers.

## 0.4.0 (2026-10-28)

- `ois-record`: write artifact, actor, evidence, decision and finding records, supersede a
  finding and bundle records. Digests of files and commits, the Git location of a file, a
  decision's subject digest and ids are computed; each command prints the reference of its record.
- `ois-record` refuses what the rules forbid: a changed record under an existing id, a wrong
  subject or outcome for a well-known gate, a second successor of a finding and a finding
  resolution confirmed by an actor who took part in it (R14).

## 0.3.0 (2026-10-28)

- `ois-passport build`: assemble a validated, unsigned Quality Passport from records, bundles
  and directories.

## 0.2.0 (2026-10-28)

- `ois-intent`: seal, approve, version and compare intents written in YAML or JSON.
- Docs: `docs/architecture.md` and `docs/development.md`; the README covers usage only.
- Tests: `test_robustness.py` points every reference of every valid fixture at every record.
- `ois-validator --version` and `ois-junit --version`.
- Docs: privacy section; both commands open no network connection.
- Issue and pull request templates; changes to what the validator reports link to the
  specification repository.

## 0.1.0 (2026-10-28)

First release. Targets OIS specification v0.1, working draft 4, and the conformance fixtures at
specification commit `c10b028`.

- `ois-validator` checks records, bundles and Quality Passports against Appendix B and every rule
  of section 5 that can be checked from the records at hand: R1–R5 and R8–R15, plus the proposed
  R16 (release decision) and R17 (digests). Reports carry rule ID, level and JSONPath.
- Signatures are not verified yet; R12 reports every recorded context as self-declared.
- `ois-junit` turns JUnit XML into a Level 1 bundle (specification section 11.4) and validates it
  before writing.
- Python API: `ois_validator.validate()`.
