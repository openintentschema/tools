# Development

How to work on the validator. How it is built: [architecture.md](architecture.md). Rules for
contributions: [CONTRIBUTING.md](../.github/CONTRIBUTING.md).

## Setup and checks

Requires [uv](https://docs.astral.sh/uv/); it installs the Python version in `.python-version`.

```bash
uv sync                              # .venv from uv.lock
uv run pytest                        # all tests
uv run ruff check && uv run ruff format --check
uv run mypy src tests scripts        # strict
```

CI (`.github/workflows/checks.yml`) runs the same on Python 3.13, 3.14 and 3.15, plus a job that
checks the copies from the specification (below) and one that builds the container image and runs
commands in it without network. Locally:

```bash
docker build -t ois-validator:dev .
docker run --rm --network none -v "$PWD:/work" --user "$(id -u):$(id -g)" ois-validator:dev \
  ois-validator tests/fixtures/valid/passport.json
```

## Layout

```
src/ois_validator/   the package (modules: architecture.md)
  schemas/           copy of Appendix B
Dockerfile           the container image (.dockerignore keeps the build context small)
tests/
  fixtures/          copy of the conformance fixtures, run by test_fixtures.py
  examples/          copy of the Appendix A examples
  test_*.py          unit tests per module; test_robustness.py points references anywhere
scripts/sync_spec.py copies schema, examples and fixtures from the specification
spec.lock            the specification commit the copies come from
docs/                this file and architecture.md
.github/             CI and release workflows, templates, contributing guide, Code of Conduct,
                     security policy
```

## Copies from the specification

The schema, the examples and the conformance fixtures are copies from the
[specification repository](https://github.com/openintentschema/openintentschema). Never edit
them here. To update them, check out the specification next to this repository, commit there,
and run:

```bash
uv run scripts/sync_spec.py ../openintentschema
```

The script refuses a checkout with uncommitted changes and records its commit in `spec.lock`.
CI checks out the specification at that commit and fails if a copy differs
(`sync_spec.py --check`). Push the specification first, then this repository.

## Changing what the validator reports

What the validator accepts and reports is defined by the specification and its fixtures, so a
change starts there:

1. **Specification:** an RFC issue for a new or changed rule. Fixtures for it are added to
   `fixtures/generate.py`; their expected reports go into `fixtures/manifest.json` by running the
   generator. How the rule is read goes into `fixtures/README.md`.
2. **Sync** the fixtures here and watch them fail: `tests/test_fixtures.py` compares every
   fixture with the manifest for the rules in `IMPLEMENTED`.
3. **Implement** the rule in `rules.py` as one function `Document -> Iterator[Report]`, in rule
   order, and register it in `RECORD_RULES` (applies to single records too) or
   `CONTAINER_RULES`. Check a resolved record's `type` before reading its fields, and skip what
   an unresolved reference hides (R10 reports it).
4. **Enable** a new rule in `IMPLEMENTED` once its fixtures pass.

A bug that is not a question of reading the specification gets a unit test here first, then the
fix.

## Releases

1. Add the changes under a new version in `CHANGELOG.md` and set `version` in `pyproject.toml`.
2. Merge to `main` and wait for CI.
3. Push a tag: `git tag v<version> && git push origin v<version>`.

The workflow `.github/workflows/release.yml` checks that tag and version match, builds the
package, smoke-tests the installed wheel and publishes to PyPI with trusted publishing: no
stored token, and PyPI records signed attestations of where the package was built. After the
PyPI job, it builds the container image from the `Dockerfile` for linux/amd64 and linux/arm64,
pushes it to `ghcr.io/openintentschema/ois-validator` (tags `<version>`, `<major>.<minor>`,
`latest`) with an SBOM, attests its build provenance and runs it once without network. The
`pypi` environment asks a maintainer to approve each release and accepts only tags `v*`
(*Settings → Environments → pypi*). A published version cannot be replaced; fix forward with a
new version.

The first image push creates the package on GitHub as private. Make it public once: organization
*Packages → ois-validator → Package settings → Change visibility*, and check that it is linked to
this repository (the image label `org.opencontainers.image.source` does that).

After a release, update the guides on the website (specification repository,
`site/src/content/docs/`) that describe a changed command, and install the new version from PyPI
once to check it (`uvx --refresh --from ois-validator==<version> …`; shields.io caches the PyPI
badge for up to six hours).

## Conventions

- Newest released versions of Python, libraries, tools and GitHub Actions, looked up rather than
  remembered. Actions are pinned to a commit SHA, base images in the `Dockerfile` to a digest,
  each with the version as a comment or tag. Dependencies are
  updated by hand, without bots.
- Python 3.13 or later, ruff with line length 100, mypy strict, pytest.
- English in code and documentation; rule IDs and levels exactly as in the specification.
- Every commit carries a DCO sign-off: `git commit -s -m "<what changed>"`, one line.
- No network access at run time: commands read local files and the local Git checkout only.
  Verify a new feature in a sandbox that blocks the network before stating otherwise.
