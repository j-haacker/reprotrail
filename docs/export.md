# Export and transfer

Export selected datasets and all linked upstream reproduction metadata. Only the
selected data are included, through symlinks; intermediate products and original
source datasets stay external. Metadata files are copied independently.
An export is not a full offline environment or a copy of original source data.

The examples below use a fictional survey-processing project with separate
`survey-data` and `survey-tools` repositories.

```bash
reprotrail export \
  --provenance /data/survey-data/results/north/summary/summary.prov.json \
  --provenance /data/survey-data/results/south/summary/summary.prov.json \
  --input-root survey-data=/data/survey-data \
  --input-root survey-tools=/code/survey-tools \
  --product-base /data/survey-data/results \
  --output /scratch/survey-export
```

The selected packages are easy to find under `products/`:

```text
survey-export/
  products/
    north/summary/             # Selected dataset and its package sidecars
    south/summary/
  metadata/                    # Upstream reproduction artifacts
  repositories/                # Present only with --archive-repos
  manifest.json
  README.md                    # Links to selected datasets and provenance
```

`--product-base` names a source directory, not a destination. Selected packages
are placed relative to that directory; every selected provenance package must
lie beneath it. Without this option, `products/` preserves repository-relative
paths, such as `products/survey-data/results/north/summary/`. Packages without a
known repository root retain digest-based paths under `products/artifacts/`.
Only the selected packages move into `products/`; intermediate products remain
metadata-only. The manifest records all actual locations.

Existing exports using the older `metadata/` layout remain readable and are not
rearranged. Create a new export to use the new layout.

Run this command on the source SSH host. Pull from the receiving host using:

```bash
rsync -aL --partial host:/scratch/survey-export/ ./survey-export/
```

`-L` dereferences selected dataset symlinks. The source datasets must remain
available and unchanged during transfer. The receiving export contains actual
data and can be moved without retaining the source host's directory structure.
Reprotrail does not manage SSH credentials, transfers, or remote execution.

Original provenance files remain byte-for-byte unchanged. `manifest.json`
records the selected roots, products, dependency order, original-to-export file
locations, file checksums, external inputs, traced repositories, and metadata
gaps. Metadata is shared across selected products. Selected package sidecars
stay beside their dataset under `products/`; upstream metadata preserves
repository-relative paths under `metadata/` where an input root is known. Other
artifacts use digest paths. Input roots resolve repository-relative provenance
input paths and do not copy the corresponding datasets.

By default, missing or mismatched metadata produces an `incomplete` export and
a gap report, rather than preventing collection. `--strict` rejects it and
removes an incomplete destination. Missing or malformed root records, cycles,
ambiguous paths, and destination collisions are errors. Existing destinations
are never overwritten. `--dry-run` discovers and validates local metadata
without creating files, linking datasets, or fetching repositories. Repository
availability is therefore not proved by a dry run.

Git source persistence is opt-in with `--archive-repos`. Only repositories
explicitly recorded in `project_repo`, `software_repos`, and `configured_repos`
are bundled, at all their required revisions. Input repository ownership and
Git dependencies found only in the runtime snapshot/lockfile are excluded.
Use `--repo-source NAME=SOURCE` for local repositories or alternate remotes.
During recursive replay, bundles supply recorded checkouts and matching locked
Git dependency downloads through process-local URL mappings; the lockfile and
global Git configuration stay unchanged. Standalone Git bundles include history
needed for the recorded commits and may be larger than source snapshots.
Downloadable dependency packages remain external. Dirty tracked changes require their recorded patches; untracked code
is not reconstructed from a filename listing.

(cli-export)=
## `reprotrail export` arguments

| Syntax | Status | Behavior |
| --- | --- | --- |
| `--provenance PATH` | Required; repeatable | Selects product provenance records whose datasets are linked into the export. |
| `--output PATH` | Required | Creates a new export directory. |
| `--product-base PATH` | Optional | Places selected packages under `products/` relative to source directory PATH; all selected packages must lie beneath it. |
| `--input-root NAME=PATH` | Optional; repeatable | Resolves repository-relative inputs for recorded repository NAME under PATH. |
| `--repo-source NAME=SOURCE` | Optional; repeatable | Archives traced repository NAME from SOURCE when repository archiving is enabled. |
| `--archive-repos` | Optional | Archives explicitly traced Git repositories, deduplicating required revisions per repository. |
| `--strict` | Optional | Rejects missing or mismatched reproduction metadata. |
| `--dry-run` | Optional | Inspects collection without creating files or fetching repository archives. |
| `--json` | Optional | Prints the manifest and gap report as JSON. |

## Python API

```python
from reprotrail.export import export_products

report = export_products(
    provenances=["/data/survey-data/results/north/summary/summary.prov.json"],
    output="/scratch/survey-export",
    product_base="/data/survey-data/results",
    input_roots={"survey-data": "/data/survey-data", "survey-tools": "/code/survey-tools"},
    dry_run=True,
)
```

Use `discover_products` for dependency discovery without an export destination.
