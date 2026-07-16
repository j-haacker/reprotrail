# reprotrail

`reprotrail` records the command, software state, inputs, runtime, and product
metadata behind data-processing results. Use it to audit how a result was made,
detect unacknowledged dependency changes, or prepare a workspace that restores
the recorded source and Pixi environment.

Reprotrail is Pixi-first. Core provenance capture remains useful without Pixi,
but complete runtime snapshots, dependency epochs, Git dependency freshness,
and reproduction expect a [Pixi](https://pixi.sh/) project and lockfile.

## Install

Reprotrail requires Python 3.11 or newer and Git. Install the full product
metadata feature set from PyPI:

```bash
python -m pip install "reprotrail[products]"
```

Use `python -m pip install reprotrail` for the minimal package. Pixi is an
external executable and must be installed separately for Pixi-backed features.
After the conda-forge recipe is accepted, `conda install -c conda-forge
reprotrail` will install the complete workflow, including Pixi and product
metadata dependencies.

## First recorded command

Run this from a clean Git project:

```bash
reprotrail run \
  --log results/example.log \
  --provenance-json results/example.prov.json \
  -- python -c "from pathlib import Path; Path('results').mkdir(exist_ok=True); Path('results/example.txt').write_text('done')"
```

The wrapped command creates `results/example.txt`; reprotrail creates the log
and a versioned JSON provenance record. See {doc}`introduction` for product
sidecars, dependency checks, and reproduction.

:::{warning}
Provenance records are not guaranteed to be anonymous or secret-free. They can
contain command arguments, repository URLs, paths, dirty-file information, and
whitelisted environment values. Review records before publishing them.
:::

```{toctree}
:maxdepth: 1

introduction
concepts
cli
provenance
runner
products
pixi
epochs
reproduce
configuration
snakemake
api
releasing
```

Development setup and contribution checks are documented in the repository's
[contributor guide](https://github.com/j-haacker/reprotrail/blob/main/CONTRIBUTING.md).
