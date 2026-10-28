# AGENTS.md

Guidance for coding agents and new contributors. Start with [README.md](README.md).

## What this repository is

The reference validator of the Open Intent Schema (OIS) and the commands that write OIS records
(`ois-junit`, `ois-intent`, `ois-record`, `ois-passport`, `ois-coverage`), published on PyPI as
`ois-validator` (Python 3.13+, Apache-2.0).

The standard lives in [openintentschema/openintentschema](https://github.com/openintentschema/openintentschema),
usually checked out next to this one as `../openintentschema`. **The validator implements the
specification; it never defines it.** What is valid, which rule applies and at which level is
decided there; `fixtures/README.md` there fixes how each rule is read. If the two disagree, the
specification wins and a gap is fixed there by RFC, not worked around here. Nothing here may
depend on, name or favour a product or vendor.

## Where things are

| Path | Content |
| --- | --- |
| `src/ois_validator/` | the package; one module per concern, see [docs/architecture.md](docs/architecture.md) |
| `tests/` | unit tests per module; `fixtures/` and `examples/` are copies from the specification |
| `scripts/sync_spec.py`, `spec.lock` | copy schema, examples and fixtures; the specification commit they come from |
| `docs/` | [architecture.md](docs/architecture.md) (design decisions), [development.md](docs/development.md) (setup, adding a rule, releases) |
| `.github/` | CI and release workflows, templates, contributing guide, Code of Conduct, security policy |

## Commands

```bash
uv sync                                         # .venv from uv.lock
uv run pytest                                   # all tests, including every conformance fixture
uv run ruff check && uv run ruff format --check
uv run mypy src tests scripts                   # strict
uv run scripts/sync_spec.py ../openintentschema # update the copies from a clean checkout
```

## Rules for changes

- Never edit the copies in `tests/fixtures/`, `tests/examples/` or `src/ois_validator/schemas/`;
  change the specification repository and sync.
- A new or changed rule starts as an RFC there; then fixtures, sync, implementation, and the rule
  in `IMPLEMENTED` of `tests/test_fixtures.py` (see docs/development.md).
- Tests first; a bug fix starts with a test that shows the bug.
- Every command validates what it writes and opens no network connection.
- Keep `docs/architecture.md` current with design decisions, `CHANGELOG.md` with user-visible
  changes, and the README and the website guides with every changed command.
- English everywhere; rule IDs and levels exactly as in the specification.
- Newest released versions of Python, libraries, tools and GitHub Actions, looked up rather than
  remembered; Actions pinned to a commit SHA with the version as a comment; no update bots.
- Commits: one line, with DCO sign-off: `git commit -s -m "<what changed>"`.
