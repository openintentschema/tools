# Changelog

All notable changes to `ois-tools`, called `ois-validator` up to 0.5.0. Versions follow
[Semantic Versioning](https://semver.org/); during 0.x any release may change behaviour.

## Unreleased

## 0.8.0 (2026-10-28)

- Keyless signatures (Sigstore): `ois validate`, `ois coverage` and `ois passport build` read
  Sigstore bundles, as `actions/attest`, cosign and sigstore-python write them, and verify them
  offline against the trusted roots given with `--trusted-root` (JSON, or the JSON lines of
  `gh attestation trusted-root`). A verified signature counts for the actor whose
  `identity.issuer` and `identity.subject` equal the certificate's OIDC issuer and subject, for
  example a GitHub Actions workflow; R12 and the attested coverage follow.
- `ois trust update --output FILE` fetches a Sigstore trusted root through TUF: the public-good
  instance's by default, an organization's own with `--tuf-url` and, the first time,
  `--tuf-root`. It is the only command that opens a network connection, and only when run.
- `ois passport build --keyless-signer ISSUER=SUBJECT` counts a signer that signs the passport
  afterwards, for example with `actions/attest`, in the attested coverage.
- Optional dependency: `pip install ois-tools[sigstore]`; the container image includes it.
- Passes the keyless conformance fixtures: a passport signed by GitHub Actions through the
  public-good Sigstore instance, recorded in the specification repository, and a tampered copy.

## 0.7.0 (2026-10-28)

- DSSE signatures (specification section 8.3). `ois key generate` writes an Ed25519 key pair and
  prints its key id; `ois sign` wraps a bundle or passport in a DSSE envelope or adds a signature;
  `ois passport build --sign KEY` builds and signs in one step, so the attested coverage inside
  the passport counts the keys that sign it. Ed25519 and ECDSA P-256 keys.
- `ois validate`, `ois coverage` and `ois passport build` read DSSE envelopes. `--trusted-key`
  names the public keys to verify with (a file, a directory of `*.pub`, or `KEYID=FILE`).
  Signatures are reported under the proposed rule ID `signature`: a violation for an envelope
  without signatures, with the wrong payload type or with a signature that does not verify; a
  warning for a key that is not trusted.
- R12 counts a context as attested when its recorder's `identity.keyId` signed the document under
  a trusted key; `intentCoverageAttested` follows. Certificate identities (Sigstore keyless) are
  not verified yet.
- `ois record actor --public-key FILE` sets the actor's key id from its public key.
- Passes the signed conformance fixtures; `scripts/sync_spec.py` copies their public test keys.
- New dependency: `cryptography`.

## 0.6.0 (2026-10-28)

- **Renamed** from `ois-validator` to `ois-tools`: the package holds the validator and the tools
  that write records. `pip install ois-tools`; the Python module is `ois_tools`; the repository
  is `openintentschema/tools`. `ois-validator` stays at 0.5.0.
- **One command, `ois`,** with subcommands replaces the six commands: `ois validate`
  (was `ois-validator`), `ois junit`, `ois intent`, `ois record`, `ois passport` and
  `ois coverage`. Their options are unchanged; `ois --version` replaces the per-command option.
- Container image `ghcr.io/openintentschema/ois-tools` with `ois` as its entrypoint and git, for
  projects in any language (linux/amd64 and linux/arm64, with build provenance and SBOM).
  Published by the release workflow after the PyPI release.
- `ois record artifact --commit` names the artifact after the origin repository instead of the
  checkout's folder, which differs between clones and containers.
- The JUnit adapter's actor is named "ois junit (JUnit XML adapter of ois-tools)"; its id stays
  `<namespace>/actors/ois-junit`.

## 0.5.0 (2026-10-28)

- Intent coverage by the reference method of Appendix D: `intentCoverage` and the variants
  `intentCoverageAttested`, `intentCoverageWeighted` and `intentCoverageDiverse`
  (`ois_validator.coverage`).
- `ois-passport build` records them in the passport's `assessments`; `--no-coverage` leaves them
  out. Signatures are not verified yet, so `intentCoverageAttested` is 0 wherever evidence rests
  on a context (R12).
- `ois-coverage`: per criterion, what counts and why other evidence does not; for a passport, a
  check of the coverage it declares (exit code 1 if it differs).

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
