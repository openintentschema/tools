# Changelog

`ois-tools`, called `ois-validator` up to 0.5.0. [Semantic Versioning](https://semver.org/);
during 0.x any release may change behaviour.

## Unreleased

## 0.9.0 (2026-10-29)

- GitHub Action: `uses: openintentschema/tools@v0.9.0` installs `ois` in its version.
- GitLab CI template `ci/gitlab/ois.gitlab-ci.yml` with `.ois` and `.ois-junit`.

## 0.8.0 (2026-10-28)

- Sigstore keyless signatures: Sigstore bundles verified offline with `--trusted-root`; the
  signer is the certificate identity (`identity.issuer`, `identity.subject`).
- `ois trust update`: fetches a Sigstore trusted root through TUF; the only command with network
  access.
- `ois passport build --keyless-signer ISSUER=SUBJECT` for signing with `actions/attest`.
- Optional extra `ois-tools[sigstore]`, included in the container image.
- Passes the keyless conformance fixtures.

## 0.7.0 (2026-10-28)

- DSSE signatures with Ed25519 or ECDSA P-256 keys: `ois key`, `ois sign`,
  `ois passport build --sign`; verification with `--trusted-key`.
- Proposed rule ID `signature`; R12 counts contexts signed by their recorder as attested.
- `ois record actor --public-key`.
- Passes the signed conformance fixtures. New dependency: `cryptography`.

## 0.6.0 (2026-10-28)

- Renamed to `ois-tools` (module `ois_tools`, repository `openintentschema/tools`);
  `ois-validator` stays at 0.5.0.
- One command `ois` with subcommands replaces the six commands; options are unchanged.
- Container image `ghcr.io/openintentschema/ois-tools` (linux/amd64, linux/arm64) with build
  provenance and SBOM.
- `ois record artifact --commit` names the artifact after the origin repository.

## 0.5.0 (2026-10-28)

- Intent coverage by the reference method of Appendix D, with its variants.
- `ois-passport build` records it in `assessments`; `--no-coverage` leaves it out.
- `ois-coverage`: coverage per criterion; checks what a passport declares.

## 0.4.0 (2026-10-28)

- `ois-record`: artifact, actor, evidence, decision and finding records, finding chains and
  bundles, with computed digests and ids; refuses changed records and R14 breaches.

## 0.3.0 (2026-10-28)

- `ois-passport build`: assemble a validated Quality Passport.

## 0.2.0 (2026-10-28)

- `ois-intent`: seal, approve, version and compare intents in YAML or JSON.
- `--version` for every command.

## 0.1.0 (2026-10-28)

- `ois-validator`: Appendix B and rules R1–R5, R8–R17 for records, bundles and passports.
- `ois-junit`: JUnit XML to a Level 1 bundle.
- Python API `ois_validator.validate()`.
