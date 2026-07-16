# Command-line interface

The `reprotrail` command-line interface groups seven executable commands into
five common provenance workflows. Run `reprotrail --help` to list top-level
commands, or append `--help` at any group or command level for local guidance.

## Command tree

```text
reprotrail
├── run
├── finalize
├── template
│   └── readme
├── reproduce
├── epoch
│   ├── check
│   └── audit
└── pixi
    └── check-git-freshness
```

## Run commands with provenance

`reprotrail run` wraps a data-processing command, captures its runtime and Git
state, snapshots declared inputs, and writes product provenance and logs.

```bash
reprotrail run \
  --log results/run.log \
  --provenance-json results/product.prov.json \
  --input data/source.nc \
  -- python -m my_project.step --output results/product.zarr
```

See the {ref}`reprotrail run argument reference <cli-run>` for input placement,
output inference, and repository-state safeguards.

## Finalize product metadata

`reprotrail finalize` completes the checksum, README, licensing, and RO-Crate
sidecars for an existing provenance record. The related `reprotrail template
readme` command exports the bundled README template for customization.

```bash
reprotrail finalize --provenance-json results/product.prov.json
```

See the {ref}`reprotrail finalize argument reference <cli-finalize>` and
{ref}`reprotrail template readme argument reference <cli-template-readme>`.

## Reproduce a recorded run

`reprotrail reproduce` restores recorded repositories and the locked Pixi
environment into a fresh workspace, validates provenance artifacts, and can
optionally execute the recorded command.

```bash
reprotrail reproduce \
  --provenance results/product.prov.json \
  --workspace /tmp/product-reproduction \
  --env dev
```

See the {ref}`reprotrail reproduce argument reference <cli-reproduce>` for
strict validation, repository sources, input remapping, and workspace reuse.

## Manage dependency epochs

`reprotrail epoch check` compares the current runtime snapshot with the accepted
snapshots for a run root. `reprotrail epoch audit` reports how product provenance
records relate to those accepted epochs.

```bash
reprotrail epoch check \
  --run-root results/run \
  --acceptance-reason "validated smoke metrics"
```

See the {ref}`reprotrail epoch check argument reference <cli-epoch-check>` and
{ref}`reprotrail epoch audit argument reference <cli-epoch-audit>`.

## Check Pixi Git dependency freshness

`reprotrail pixi check-git-freshness` reports whether selected Git-backed Pixi
packages would move during a dry-run lockfile update without modifying the
lockfile or environment.

```bash
reprotrail pixi check-git-freshness \
  --env analysis \
  --package example-library \
  --package reprotrail
```

See the {ref}`Pixi Git freshness argument reference <cli-pixi-check-git-freshness>`
for manifest selection, repeatable packages, JSON output, and exit statuses.
