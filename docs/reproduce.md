# Reproduce

`reprotrail reproduce` creates a fresh workspace from a v1 product provenance
sidecar. It validates the product checksum, restores the recorded `project_repo`
Git checkout, copies provenance artifacts, restores the Pixi lockfile, and
rewrites recorded editable dependency paths into `repos/<name>` when
editable-local provenance is present. Legacy records without `project_repo` fall
back to the first `software_repos` entry as the project checkout.

```bash
reprotrail reproduce \
  --provenance results/product/product.prov.json \
  --workspace /tmp/product-reproduction \
  --env dev
```

Use `--execute` to run the recorded command through Pixi after setup. Use
`--repo-source NAME=PATH_OR_URL` when a recorded repository has no cloneable
remote URL.
