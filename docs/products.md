# Products

`reprotrail.products` manages durable product sidecars:

- `<stem>.prov.json`
- `<stem>.prov.json.sha256`
- `README.md`
- `LICENSE.md` when an explicit product license is selected
- `ro-crate-metadata.json`

Product packages do not default to a license. If no product license is selected,
reprotrail still writes the provenance checksum, README, and RO-Crate metadata
with a warning, but it skips `LICENSE.md` and omits `license` from provenance.

Product-specific metadata is selected from a project-root
`reprotrail.products.toml` file. Entries match product output paths with
project-relative globs:

```toml
[[products]]
output = "results/**/*.zarr"
license = "CC-BY-4.0"
readme_template = "docs/product-readme.md.template"

[[products.inputs]]
path = "data/source.zarr"
name = "Observed dataset"
producer = "Climate Center"
license = "CC-BY-4.0"
url = "https://example.invalid/source"

[[products.inputs]]
name = "Marginal lookup table"
producer = "Lookup Producer"
marginal = true

[[products.software]]
name = "workflow-lib"
kind = "package"
license = "MIT"
```

The product `license` field is an SPDX expression. When selected, reprotrail
stores a short provenance summary with `spdx`, `name`, and `url`. Input and
software license evidence stays in `ro-crate-metadata.json`.

If multiple entries match one output path, finalization fails explicitly. If no
entry matches, finalization warns and continues without a product license.

For input attribution, non-marginal inputs require `name` and `producer`.
`marginal = true` suppresses README attribution and CC-family unknown-input
license warnings for that input. When a CC-family product license is selected
and a non-marginal input license is unknown, reprotrail warns in the README and
run/finalize warning surfaces.

Software license evidence is collected locally from manual overrides,
`pixi list --json --no-install`, and project metadata when available. Manual
overrides win, and non-SPDX discovered license strings are preserved as raw
evidence in the RO-Crate.

Finalize a sidecar:

```bash
reprotrail finalize --provenance-json results/product.prov.json
```

If `rocrate` or `spdx-tools` are unavailable, finalization fails by default.
Use `--allow-partial-metadata` to write the checksum and README warning while
skipping RO-Crate/SPDX-derived outputs.

Export the bundled README template for customization:

```bash
reprotrail template readme --output docs/product-readme.md.template
```

The template uses Python `string.Template` placeholders such as
`${files_section}`, `${license_section}`, `${attribution_section}`, and
`${warnings_section}`.

## Adapting existing projects

Projects that previously used `[tool.reprotrail.license]` must move product
license selection into `reprotrail.products.toml`.

1. Remove the old project-wide license table from `pyproject.toml`:

   ```toml
   [tool.reprotrail.license]
   spdx = "MIT"
   name = "MIT License"
   url = "https://opensource.org/license/mit/"
   ```

2. Add a project-root `reprotrail.products.toml` file with one entry per output
   pattern:

   ```toml
   [[products]]
   output = "results/**/*.zarr"
   license = "MIT"
   ```

3. Add non-marginal attribution inputs with at least `name` and `producer`.
   Include `license` when known:

   ```toml
   [[products.inputs]]
   path = "data/source.zarr"
   name = "Source dataset"
   producer = "Source producer"
   license = "CC-BY-4.0"
   ```

4. Mark inputs as marginal when they should not appear in README attribution or
   CC-family unknown-license warnings:

   ```toml
   [[products.inputs]]
   name = "Lookup table"
   producer = "Workflow team"
   marginal = true
   ```

5. Add software license overrides only when local discovery is missing or wrong:

   ```toml
   [[products.software]]
   name = "workflow-lib"
   kind = "package"
   license = "MIT"
   ```

6. Install product metadata dependencies in environments that finalize product
   packages:

   ```bash
   uv sync --extra products
   ```

7. Update automation that intentionally accepts partial metadata to pass
   `--allow-partial-metadata`. Without that flag, missing RO-Crate/SPDX tooling
   fails finalization.

8. Run one product finalization and inspect the generated README, `LICENSE.md`
   when selected, provenance `license` summary, and `ro-crate-metadata.json`:

   ```bash
   reprotrail finalize --provenance-json results/product.prov.json
   ```

When optional product dependencies are installed, Zarr and NetCDF outputs also
receive lightweight pointer attributes for the provenance file, checksum, and
schema version.
