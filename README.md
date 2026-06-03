# reprotrail

`reprotrail` captures enough provenance to audit and recreate data-processing
runs: software Git state, input path state, Pixi runtime snapshots, product
sidecars, dependency epochs, and reproduction workspaces.

The package is intentionally workflow-agnostic. Projects can call the Python
APIs directly or wrap commands with `reprotrail run`.

Documentation is in `docs/` and builds with:

```bash
sphinx-build -W -b html docs docs/_build/html
```

For development:

```bash
uv sync --extra dev
uv run --extra dev pre-commit install --hook-type pre-commit --hook-type pre-push
```
