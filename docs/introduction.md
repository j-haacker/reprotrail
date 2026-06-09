# Using reprotrail in practice (Introduction)

`reprotrail` records the practical trail behind a data-processing output: the
command that ran, Git state of configured software repositories (see below), selected input
path state, Pixi runtime metadata, product sidecars, dependency epochs (see below), and
enough information to set up a reproduction workspace.

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

## Git state of configured software repos

`repos` in `[tool.reprotrail]` tells reprotrail which software repositories to
inspect. For each configured repo, reprotrail records stable identifiers such as
commit, branch, remote URL, dirty status, and, when allowed, compact dirty-state
evidence. Dirty repositories block execution by default; pass `--allow-dirty`
only when that state is intentional and should be part of the record.

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
versions, platform identity, and editable dependency Git state. If the runtime
changes, `reprotrail epoch check` can stop the workflow until the change is
accepted with a reason.

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
