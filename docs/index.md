# reprotrail

`reprotrail` is a reusable Python package for recording provenance, capturing
Pixi runtime state, creating product sidecars, guarding dependency epochs, and
setting up reproduction workspaces.

```{toctree}
:maxdepth: 2

provenance
runner
products
epochs
reproduce
configuration
snakemake
api
migration/index
```

## Install for development

```bash
uv sync --extra dev
```

Build the documentation with warnings treated as errors:

```bash
sphinx-build -W -b html docs docs/_build/html
```
