# reprotrail

**Trace a data product back to the command, code, inputs, and environment that made it.**

[![CI](https://github.com/j-haacker/reprotrail/actions/workflows/ci.yml/badge.svg)](https://github.com/j-haacker/reprotrail/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-online-4c72b0)](https://j-haacker.github.io/reprotrail/)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)
![License: MIT](https://img.shields.io/badge/license-MIT-2b9348)
![Status: alpha](https://img.shields.io/badge/status-alpha-e67e22)

`reprotrail` is a Python command-line tool and library for recording the
practical evidence behind data-processing runs. Wrap an existing command and
it writes a readable, machine-checkable trail: what ran, which Git revision and
inputs it used, available Pixi environment state, and which files belong to the
resulting product.

```text
command + inputs + Git + pixi.lock
                  │
           reprotrail run
                  │
                  ▼
       result + log + provenance
             + product metadata
```

| Capture | Guard | Package | Reproduce |
| --- | --- | --- | --- |
| Record commands, inputs, Git state, and the locked runtime. | Stop on dirty code or unacknowledged dependency drift. | Add checksums, attribution, licensing, and RO-Crate sidecars. | Restore recorded commits and prepare the locked Pixi workspace. |

Reprotrail is workflow-agnostic: use it from a shell, Snakemake, another
workflow system, or Python. It records reproducibility evidence around your
domain logic instead of replacing it.

## See it in one command

From a clean Git project, wrap a product-producing command:

```bash
reprotrail run \
  --log results/output.log \
  --provenance-json results/output.prov.json \
  --product-output results/output.zarr \
  --input data/source.nc \
  -- python -m my_project.step --output results/output.zarr
```

The workflow still creates `results/output.zarr`. Reprotrail adds:

- `output.log` — combined standard output and error from the command;
- `output.prov.json` — versioned command, input, software, runtime, and status
  metadata;
- `output.prov.json.sha256` — an integrity checksum for the provenance record;
- `README.md` and `ro-crate-metadata.json` — a human summary and structured
  product relationships; and
- `LICENSE.md` — only when the product has an explicit configured license.

If the command fails, the provenance record still explains what was attempted
and how it ended.

## Install

Reprotrail requires Python 3.11 or newer and Git. The recommended installation
includes product metadata support:

```bash
python -m pip install "reprotrail[products]"
```

For command and provenance capture without RO-Crate, SPDX, xarray, and Zarr
support, install the minimal package:

```bash
python -m pip install reprotrail
```

[Pixi](https://pixi.sh/) is an external executable, not a Python dependency.
Basic provenance capture works without it; runtime snapshots, dependency
epochs, Git dependency freshness checks, and the complete reproduction workflow
expect a Pixi project and lockfile.

After the conda-forge recipe is accepted, the full Pixi-first installation will
be available with:

```bash
conda install -c conda-forge reprotrail
```

## What reprotrail helps answer

- **What made this file?** Inspect the recorded command, timestamps, inputs,
  Git commits, and runtime packages.
- **Did the software change between runs?** Use dependency epochs to reject or
  explicitly accept a new runtime snapshot.
- **Can someone understand this product without the original workflow?** Ship a
  README, checksum, attribution, license evidence, and RO-Crate metadata beside
  it.
- **Can I rebuild it later?** Prepare a fresh workspace from the recorded
  repositories and Pixi lockfile, validate the evidence, then choose whether to
  execute the command.

## Deliberate limits

Reprotrail records and checks evidence; it does not make a non-deterministic
program deterministic, archive remote repositories or input data, or guess a
license for a generated product. Reproduction still depends on recorded
commits, package sources, and inputs remaining accessible.

> **Review provenance before publishing it.** Records can contain command
> arguments, repository URLs, input paths, dirty-file information, and
> explicitly whitelisted environment values. Portable “public” helpers remove
> selected local roots; they are not secret scanners or anonymizers.

## Documentation

- [Start with the practical workflow](https://j-haacker.github.io/reprotrail/introduction.html)
- [Learn the concepts and terminology](https://j-haacker.github.io/reprotrail/concepts.html)
- [Browse the command-line interface](https://j-haacker.github.io/reprotrail/cli.html)
- [Configure a project](https://j-haacker.github.io/reprotrail/configuration.html)
- [Read the Python API reference](https://j-haacker.github.io/reprotrail/api.html)

Reprotrail is currently alpha software. Module-level Python APIs may change
during the `0.x` series; the CLI and explicitly versioned provenance schemas are
the clearer compatibility contracts. See the
[changelog](https://github.com/j-haacker/reprotrail/blob/main/CHANGELOG.md)
before upgrading.

Contributions are welcome; start with the
[contributor guide](https://github.com/j-haacker/reprotrail/blob/main/CONTRIBUTING.md).
