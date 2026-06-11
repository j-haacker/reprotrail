# Using reprotrail in practice (Introduction)

`reprotrail` records the practical trail behind a data-processing output: the
command that ran, Git state for the workflow repository and active editable
runtime dependencies, selected input path state, Pixi runtime metadata, product
sidecars, dependency epochs (see below), and enough information to set up a
reproduction workspace.

It is intentionally workflow-agnostic, but there are Python-based
convenience options. Use it around a shell command, inside Snakemake, or
through Python APIs. Your workflow still owns domain logic, input
selection, resources, and output naming.

## General idea

- Run product-producing commands through `reprotrail run`.
- Let reprotrail write a provenance sidecar and, when possible, product package
  files such as `README.md`, checksum, license metadata, and RO-Crate metadata.
- Archive / track provenance records instead of / along with data checksums.
- Revisit provenance records if in doubt.
- Use `reprotrail reproduce` if data got corrupted or went missing.

## What users need to do

1. Add `[tool.reprotrail]` to `pyproject.toml`.
2. Add `reprotrail.products.toml` if products need license, README,
   attribution, or software license metadata.
3. Wrap each durable-output command with `reprotrail run`.
4. Pass `--provenance-json` and, when relevant, `--product-output`.
5. Inspect the generated `.prov.json`, checksum, README, and license metadata.
6. Add `reprotrail epoch check` or `reprotrail epoch audit` where runtime drift
   matters.
7. Use `reprotrail reproduce` when a product needs to be recreated or audited in
   a clean workspace.

## Runtime software state

Trusted runtime software state comes from the command's active environment. The
project Git checkout is recorded as `project_repo`. Active external Pixi
editable/path dependencies are recorded as `software_repos`. Installed Python
distributions named in `package_summary` are recorded in the environment summary
and dependency snapshot as `runtime_packages`, including sanitized
`direct_url.json` source metadata when available.

`repos` in `[tool.reprotrail]` is diagnostic-only. It can list sibling checkouts
that are useful to inspect, but those repos are written under `configured_repos`
only when they are not active runtime sources. They do not satisfy runtime
provenance, do not affect dependency epochs, and do not block execution if they
are dirty.

Dirty project repos and active editable/path dependency repos block execution by
default; pass `--allow-dirty` only when that trusted runtime state is intentional
and should be part of the record.

```toml
[tool.reprotrail]
repos = [".", "../shared-utils"]
product_root_markers = ["products", "prepared", "adjusted"]
package_summary = ["my-project", "shared-utils", "xarray"]
pixi_environment = "dev"
pixi_lockfile = "pixi.lock"
```

## Dependency epochs

Dependency epochs are a lightweight contract for a run root. They record the
accepted runtime snapshot: Pixi lockfile hash, Pixi environment, selected package
versions and source metadata, platform identity, and editable dependency Git
state. Git package commit changes are included even when the package version
string stays the same. If the runtime changes, `reprotrail epoch check` can stop
the workflow until the change is accepted with a reason.

```bash
reprotrail epoch check --run-root results/run

reprotrail epoch check \
  --run-root results/run \
  --acceptance-reason "validated smoke metrics"

reprotrail epoch audit \
  --run-root results/run \
  --output results/run/qc/dependency_epochs.json
```

## Common use cases

Capture one product-producing command:

```bash
reprotrail run \
  --log results/run.log \
  --provenance-json results/product.prov.json \
  --product-output results/product.zarr \
  -- python -m my_project.step --output results/product.zarr
```

Describe product metadata:

```toml
[[products]]
output = "results/**/*.zarr"
license = "CC-BY-4.0"

[[products.inputs]]
path = "data/source.zarr"
name = "Observed source data"
producer = "BOKU-Met"
license = "CC-BY-4.0"
```

Create a reproduction workspace and run:

```bash
reprotrail reproduce \
  --provenance results/product/product.prov.json \
  --workspace /tmp/product-reproduction \
  --env dev \
  --execute
```

## Current limitations

- `reprotrail` records and checks provenance; it does not make a
  non-deterministic command deterministic.
- Pixi is the first-class runtime environment path today.
- Product licenses are never guessed; configure them in
  `reprotrail.products.toml`.
- Dirty repos and editable/path dependencies require explicit allowance, and
  reproduction may need `--repo-source`.
- Input provenance records path/backend state, not guaranteed long-term access
  to private or moved data.
