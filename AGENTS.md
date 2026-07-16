# Agent Instructions

## Working Rules

Create a new branch for each unrelated coherent implementation request,
including features, fixes, and grouped documentation changes. Always
create branches when on main. Use concise kebab-case branch names in the
form `type/short-topic`, such as `feat/product-sidecars`,
`fix/provenance-hash`, `docs/contributing-guide`, or
`chore/update-lockfile`. Commit along the way.

Use an in-repository `./worktree-<topic>` worktree for comprehensive
implementation tasks. Temporarily add the specific worktree path to
`.gitignore` without committing that ignore change. Remove such worktrees and
its ignore entry when you encounter ones that have successfully been merged
into main.

Keep changes scoped to the request. Avoid unrelated refactors, generated build
output, and metadata churn that is not needed for the task.

## Documentation and Changelog

Update documentation when public APIs, CLI behavior, configuration, provenance
output, product metadata behavior, or contributor-facing workflow changes.

Update `CHANGELOG.md` under `Unreleased` for user-facing behavior, CLI,
documentation, packaging, or compatibility changes. Keep the changelog
consistent by using existing categories, creating standard categories only when
needed, and merging duplicate or overlapping bullets instead of adding
near-duplicates. Skip changelog entries for internal-only maintenance, tests,
or refactors with no behavior change.

## Checks

Use the `uv` workflow from `CONTRIBUTING.md` for local verification:

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run --extra dev sphinx-build -W -b html docs docs/_build/html
```
