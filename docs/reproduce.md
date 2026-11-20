# Reproduce

`reprotrail reproduce` creates a fresh workspace from a v1 product provenance
sidecar. It validates the product checksum, restores the recorded `project_repo`
Git checkout, copies provenance artifacts, restores the Pixi lockfile, and
rewrites recorded editable dependency paths into `repos/<name>` when
editable-local provenance is present. Legacy records without `project_repo` fall
back to the first `software_repos` entry as the project checkout.

For each recorded Git repository, the commit hash selects the source state; the
recorded branch is not required to exist on the remote. Reprotrail clones without
checking out a remote branch and fetches the exact commit when the clone does not
already contain it. When a branch name was recorded, reproduction creates that
branch locally at the recorded commit without an upstream. Records without a
usable branch name are checked out with a detached HEAD.

Reproduction invokes the external `pixi` executable and may contact recorded
Git remotes and package channels. Preparing a workspace does not execute the
recorded command unless `--execute` is supplied. Inspect the restored sources,
input mappings, warnings, and `REPRODUCTION.md` before executing provenance from
an untrusted source.

An exact hash identifies a commit but does not archive it. Reproduction cannot
restore a commit after the repository source has garbage-collected it or refuses
to serve it. Legacy or incomplete records without a commit fall back to their
recorded branch, or to the source's default branch, and add a warning to the
reproduction report. This warning causes `--strict` reproduction to fail.

(cli-reproduce)=
## `reprotrail reproduce` arguments

```bash
reprotrail reproduce \
  --provenance results/product/product.prov.json \
  --workspace /tmp/product-reproduction \
  --env dev
```

Use `--execute` to run the recorded command through Pixi after setup. Use
`--repo-source NAME=PATH_OR_URL` when a recorded repository has no cloneable
remote URL.

| Syntax | Status | Behavior |
| --- | --- | --- |
| `--provenance PATH` | Required | Reads the product provenance record at `PATH`. |
| `--workspace PATH` | Required | Creates the reproduction workspace at `PATH`. |
| `--execute` | Optional | Runs the recorded command after restoring the workspace and installing the locked Pixi environment. Without it, reprotrail prepares and validates the workspace but does not run the command. |
| `--strict` | Optional | Treats every reproduction warning as a failure. |
| `--env ENV` | Optional | Uses Pixi environment `ENV` instead of the environment recorded in provenance. |
| `--project-repo NAME` | Optional | Selects recorded repository `NAME` as the project checkout. This is useful for legacy or ambiguous provenance. |
| `--repo-source NAME=SOURCE` | Optional; repeatable | Uses local path or URL `SOURCE` for recorded repository `NAME`; repeat it for multiple repositories. |
| `--input-map RECORDED=LOCAL` | Optional; repeatable | Replaces exact recorded input path `RECORDED` with `LOCAL` in the reconstructed command and validation; repeat it for multiple inputs. |
| `--resume` | Optional | Reuses an existing workspace and continues restoration instead of rejecting it. |
| `--force` | Optional | Removes an existing workspace and recreates it from scratch. This takes precedence over resuming it. |
| `--json` | Optional | Prints the reproduction report as JSON instead of a human-readable summary. |
| `--recursive` | Optional | Rebuilds missing ancestors in dependency order; plans without installing or executing unless `--execute` is supplied. |
| `--bundle PATH` | Optional | Reads a transferred export directory or manifest and implies recursive mode. |
| `--accept-incomplete` | Optional | Explicitly accepts reported metadata gaps for recursive replay; missing executable prerequisites still block. |
| `--input-root NAME=PATH` | Optional; repeatable | Resolves repository-relative inputs for recorded repository NAME in original provenance or relocated external inputs. |

## Recursive reproduction

Use `--recursive` to regenerate missing ancestors before the target. Add
`--bundle` to use a transferred export; specifying it implies recursive mode.
The target can be any provenance record in the export, including an intermediate
whose data were excluded. The manifest supplies the paths, so both the current
`products/` layout and older exports with datasets under `metadata/` are supported.
Continuing the fictional survey example from
[the export guide](export.md), reproduce a cleaned intermediate dataset:

```bash
reprotrail reproduce \
  --bundle ./survey-export \
  --provenance ./survey-export/metadata/survey-data/results/north/cleaned/cleaned.prov.json \
  --workspace ./replay \
  --input-root survey-data=/archive/survey-data
```

Without `--execute`, recursive reproduction writes a dependency plan and
validation report without installing environments or running producers. Add
`--execute` after inspecting it. Original input datasets and nonarchived code
sources/packages must remain accessible, possibly through `--input-map` and
`--repo-source`. `--input-root` resolves repository-relative inputs in an original
provenance tree and relocates external input repositories when replaying an export.
An exact `--input-map` takes precedence over a repository root mapping.

Available ancestors are reused when their recorded provenance and available
identity evidence match. Reused ancestors prune their own dependencies from the
execution plan. File SHA-256 is stronger evidence than size/mtime or directory
stat manifests; the report explicitly identifies the evidence used. Checks
cannot recover content checksums absent from historical records.

Missing dependencies are produced sequentially, once each, using each step's
recorded code and environment. All outputs go into the reproduction workspace,
with separate producer checkouts and auxiliary output locations. Path tokens
are relocated using the manifest and explicit input mappings. Directory-based
inputs passed through `--data-root` use workspace overlays of links to the mapped
inputs. Recognized output flags are `--output`, `--qc`, `--done`,
`--validation-json`, and `--provenance-json`. Other command-line conventions
require explicit mappings for absolute paths or paths outside the restored checkout.
Arbitrary commands must obey these mappings: reprotrail is not an operating
system sandbox for untrusted producer code.

Regenerated records receive new hashes. Replay preserves archived originals,
substitutes the new hashes for downstream validation, and records the old-to-new
mapping in `reproduction.json`. Successful execution does not prove numerical
or byte equivalence to the historical product. Missing/mismatched metadata
blocks affected producers unless `--accept-incomplete` is supplied. This flag
records acceptance; missing inputs, lockfiles, or executable prerequisites still
block reproduction. `--strict` continues treating warnings as failures.

`--resume` checks replay signatures, observed inputs, output state, and generated
provenance before reusing completed steps. Changed inputs/options invalidate
reuse. A required producer failure stops downstream execution and leaves a
report. `--force` recreates only the specified workspace, refusing to remove an
enclosing source-data/export directory.
