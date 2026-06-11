# Configuration

Project settings live in `[tool.reprotrail]` in `pyproject.toml`.

```toml
[tool.reprotrail]
repos = [".", "../shared-utils"]
product_root_markers = ["products", "prepared", "adjusted"]
env_var_whitelist = ["OMP_NUM_THREADS", "PIXI_ENVIRONMENT_NAME"]
package_summary = ["my-project", "shared-utils", "xarray"]
pixi_environment = "dev"
pixi_lockfile = "pixi.lock"
```

CLI flags override settings for a single command where applicable.

`package_summary` selects the installed Python distributions to summarize in the
legacy `packages` version map and the richer `runtime_packages` records. For
Git-installed packages, `runtime_packages` includes sanitized `direct_url.json`
source metadata such as VCS URL and commit ID.

`repos` is diagnostic-only. It lists repositories worth observing, but runtime
software provenance is derived from the project repo and active Pixi
editable/path dependencies. Configured repos that are not active runtime sources
are recorded under `configured_repos`, not trusted `software_repos`.

`product_root_markers` are used to infer the run root from a product provenance
path. For example, with `product_root_markers = ["products"]`,
`results/run/products/a/a.prov.json` resolves to `results/run`.

Product licenses are not configured in `[tool.reprotrail]`. The old
`[tool.reprotrail.license]` table is rejected so projects do not accidentally
apply one project-wide license to every product. Use `reprotrail.products.toml`
for product-specific license, README, attribution, and software license
metadata.
