# reprotrail

`reprotrail` is a reusable Python package for recording provenance, capturing
Pixi runtime state, creating product sidecars, guarding dependency epochs, and
setting up reproduction workspaces.

```{toctree}
:maxdepth: 1

introduction
provenance
runner
products
epochs
reproduce
configuration
snakemake
api
```

## Install for development

```bash
uv sync --extra dev
uv run --extra dev pre-commit install --hook-type pre-commit --hook-type pre-push
```

Build the documentation with warnings treated as errors:

```bash
sphinx-build -W -b html docs docs/_build/html
```
