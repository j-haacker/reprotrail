# Contributing

Thank you for helping improve `reprotrail`. This guide covers the expected
workflow for small fixes, documentation updates, and feature changes.

## Getting Started

Clone the repository, then install the development dependencies:

```bash
uv sync --extra dev
```

Install the pre-commit hooks before opening a pull request:

```bash
uv run --extra dev pre-commit install --hook-type pre-commit --hook-type pre-push
```

## Local Checks

Run the checks that match your change before submitting it:

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run --extra dev sphinx-build -W -b html docs docs/_build/html
```

If you use Pixi, the equivalent shortcuts are:

```bash
pixi run test
pixi run lint
pixi run docs
```

## Contribution Expectations

Keep changes focused and avoid unrelated refactors. Add or update tests for
behavior changes, and update documentation when public APIs, CLI behavior,
configuration, provenance output, or product metadata behavior changes.

Do not commit generated build output such as `docs/_build/`.

## Changelog

Update `CHANGELOG.md` under `Unreleased` for user-facing behavior, CLI,
documentation, packaging, or compatibility changes. Internal-only maintenance,
test-only changes, and refactors that do not change behavior can skip the
changelog.

## Releases

Maintainers should follow the [release guide](docs/releasing.md) for version
checks, the tag-triggered PyPI workflow, and the post-PyPI conda-forge handoff.
Never upload a release from a local checkout or reuse a version that reached
PyPI.

## Pull Request Checklist

- Tests, linting, and documentation checks pass as relevant.
- Public behavior changes include tests.
- Documentation is updated when contributor-facing or user-facing behavior
  changes.
- `CHANGELOG.md` has been considered for user-facing changes.
