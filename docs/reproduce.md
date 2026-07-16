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

An exact hash identifies a commit but does not archive it. Reproduction cannot
restore a commit after the repository source has garbage-collected it or refuses
to serve it. Legacy or incomplete records without a commit fall back to their
recorded branch, or to the source's default branch, and add a warning to the
reproduction report. This warning causes `--strict` reproduction to fail.

```bash
reprotrail reproduce \
  --provenance results/product/product.prov.json \
  --workspace /tmp/product-reproduction \
  --env dev
```

Use `--execute` to run the recorded command through Pixi after setup. Use
`--repo-source NAME=PATH_OR_URL` when a recorded repository has no cloneable
remote URL.
