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

[tool.reprotrail.license]
spdx = "MIT"
name = "MIT License"
url = "https://opensource.org/license/mit/"
```

CLI flags override settings for a single command where applicable.

`product_root_markers` are used to infer the run root from a product provenance
path. For example, with `product_root_markers = ["products"]`,
`results/run/products/a/a.prov.json` resolves to `results/run`.
