## What and why

<!-- One topic. A change to what the validator accepts or reports needs a decided RFC in the
specification repository; link it. -->

## Checklist

- [ ] Tests: a bug fix starts with a test that fails without it
- [ ] `uv run pytest`, `uv run ruff check`, `uv run ruff format --check` and `uv run mypy src tests scripts` pass
- [ ] Copies from the specification changed only through `scripts/sync_spec.py`
- [ ] User-visible changes under "Unreleased" in `CHANGELOG.md`; docs updated
- [ ] Commits are signed off (`git commit -s`)
