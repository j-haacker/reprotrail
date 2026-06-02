# Products

`reprotrail.products` manages durable product sidecars:

- `<stem>.prov.json`
- `<stem>.prov.json.sha256`
- `README.md`
- `LICENSE.md`

Product license metadata is required before rendering `LICENSE.md`; v1 does not
default to a C4V license.

Configure license metadata in `pyproject.toml`:

```toml
[tool.reprotrail.license]
spdx = "MIT"
name = "MIT License"
url = "https://opensource.org/license/mit/"
```

Finalize a sidecar:

```bash
reprotrail finalize --provenance-json results/product.prov.json
```

When optional product dependencies are installed, Zarr and NetCDF outputs also
receive lightweight pointer attributes for the provenance file, checksum, and
schema version.
