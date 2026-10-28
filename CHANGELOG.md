# Changelog

All notable changes to `ois-validator`. Versions follow [Semantic Versioning](https://semver.org/);
during 0.x any release may change behaviour.

## Unreleased

- Docs: `docs/architecture.md` and `docs/development.md`; the README covers usage only.
- Tests: `test_robustness.py` points every reference of every valid fixture at every record.
- `ois-validator --version` and `ois-junit --version`.
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
