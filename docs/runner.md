# Runner

`reprotrail run` wraps a command and records a v1 provenance sidecar.

```bash
reprotrail run \
  --log results/run.log \
  --provenance-json results/product.prov.json \
  --product-output results/product.zarr \
  --input data/source-a.nc \
  --input data/source-b.nc \
  -- python -m my_project.step --output results/product.zarr
```

Place each repeatable `--input PATH` before the `--` separator. Arguments after
the separator belong to the wrapped command, including any child argument also
named `--input`.

The runner records:

- command, start/end time, return code, and signal failures
- project repository Git state in `project_repo`
- active external editable/path dependency Git states in `software_repos`
- diagnostic configured repository Git states in `configured_repos`
- dirty working tree policy and tracked dirty patches
- Pixi lockfile and environment summary, including `runtime_packages`, when
  `pixi.lock` is present
- dependency snapshot and accepted epoch when a contract exists
- wrapper-declared input paths, snapshotted before child execution
- product metadata when `--product-output` or wrapped `--output` is available
- product package README/license/RO-Crate sidecars when finalization succeeds

Dirty trusted runtime repositories fail before execution unless `--allow-dirty`
is set. Trusted runtime repositories are the project repo and active external
editable/path dependencies. Repos listed only in `[tool.reprotrail].repos` or
`--repo` are diagnostic candidates and do not block execution when inactive.
External editable/path Pixi dependencies fail unless `--allow-editable` is set
and the dependency resolves to a Git repository.

After the child exits, wrapper-declared inputs are merged with any `input_paths`
the child wrote to the shared provenance sidecar. Records are deduplicated by
resolved path while retaining selection, DVC/Git LFS, Git, and product
provenance metadata from either record. Declared inputs remain in failed-run
sidecars; a path that did not exist at startup is recorded explicitly with
`exists: false`, `kind: missing`, and `backend: unknown`.

The equivalent Python API accepts `inputs`:

```python
from reprotrail.runner import run_with_provenance

run_with_provenance(
    command=["python", "-m", "my_project.step"],
    log="results/run.log",
    provenance_json="results/product.prov.json",
    inputs=["data/source-a.nc", "data/source-b.nc"],
)
```

Product package finalization reads `reprotrail.products.toml` from the project
root. Use `--allow-partial-metadata` to keep the run successful when optional
RO-Crate/SPDX product metadata tools are unavailable; reprotrail will write a
README warning instead of full license-aware metadata.
