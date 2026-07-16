# reprotrail

`reprotrail` records the practical trail behind data-processing results. It
captures the command, Git state, selected inputs, Pixi runtime state, and
product metadata needed to audit a run or prepare a reproduction workspace.

The package is Pixi-first: basic provenance capture works without Pixi, while
the complete runtime snapshot and reproduction workflow expects a
[Pixi](https://pixi.sh/) project and lockfile. Reprotrail is currently alpha
software. Module-level Python APIs may change during the `0.x` series; the CLI
and explicitly versioned provenance schemas are the clearer compatibility
contracts.

## Installation

Python 3.11 or newer and Git are required. For the complete product metadata
workflow, install the `products` extra:

```bash
python -m pip install "reprotrail[products]"
```

Install the minimal package with `python -m pip install reprotrail` when only
core provenance helpers are needed. Pixi is an external executable rather than
a Python dependency; install it separately for runtime snapshots, dependency
epochs, freshness checks, and reproduction.

After the conda-forge recipe is accepted, the full workflow will be available
with:

```bash
conda install -c conda-forge reprotrail
```

## Quickstart

From a clean Git project, wrap a command and choose where its log and
provenance record should be written:

```bash
reprotrail run \
  --log results/example.log \
  --provenance-json results/example.prov.json \
  -- python -c "from pathlib import Path; Path('results').mkdir(exist_ok=True); Path('results/example.txt').write_text('done')"
```

This creates:

- `results/example.log`, containing the wrapped command's combined output;
- `results/example.prov.json`, containing the command, timestamps, Git state,
  declared input state, runtime summary, and completion status; and
- the example command's own `results/example.txt` output.

For durable data products, add `--product-output` and configure
`reprotrail.products.toml`. Reprotrail can then create a checksum, README,
license notice, and RO-Crate metadata alongside the product. It never guesses a
license for generated data.

Provenance is not automatically anonymous. Records can contain command
arguments, repository URLs, input paths, dirty-file information, and explicitly
whitelisted environment values. Review records before publishing them and
never pass secrets on recorded command lines.

## Learn more

- [Introduction and common workflows](https://j-haacker.github.io/reprotrail/introduction.html)
- [Concepts and terminology](https://j-haacker.github.io/reprotrail/concepts.html)
- [CLI overview](https://j-haacker.github.io/reprotrail/cli.html)
- [Configuration](https://j-haacker.github.io/reprotrail/configuration.html)
- [Contributor guide](https://github.com/j-haacker/reprotrail/blob/main/CONTRIBUTING.md)
- [Changelog](https://github.com/j-haacker/reprotrail/blob/main/CHANGELOG.md)
