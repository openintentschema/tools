# Contributing

Thank you for helping to build the reference validator for the Open Intent Schema.

## Before you start

- The validator implements the specification; it never defines it. What counts as valid is
  decided in [openintentschema/openintentschema](https://github.com/openintentschema/openintentschema)
  (specification, schema, conformance fixtures) under its `GOVERNANCE.md`.
- A change to what the validator accepts or reports therefore starts there, as an RFC issue.
  Bug fixes, refactoring and tooling can go straight to a pull request here.
- Nothing in this repository may depend on, name or favour a specific product or vendor.

## Developer Certificate of Origin

Every commit must carry a DCO sign-off, which states that you have the right to submit the
work under the Apache-2.0 license:

```
git commit -s -m "Describe your change"
```

## Pull requests

- One topic per pull request, with tests. A bug fix starts with a test that shows the bug.
- Do not edit the copies of the schema, examples or fixtures; update them with
  `uv run scripts/sync_spec.py` from a clean checkout of the specification repository.
- CI must pass: lint, format, strict type check and tests on every supported Python version.
- User-visible changes get a line under "Unreleased" in `CHANGELOG.md`.

[docs/development.md](docs/development.md) shows the setup, the checks and how a rule is added;
[docs/architecture.md](docs/architecture.md) how the code is organised. Security issues are
reported privately, see [SECURITY.md](SECURITY.md).

## License

By contributing you agree that your contribution is licensed under Apache-2.0.
