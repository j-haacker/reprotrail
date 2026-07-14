# Provenance

`reprotrail.provenance` records software and input state in portable metadata.
It captures Git commit, branch, canonical remote URL, dirty status, optional
dirty diff hash, and compact path state for filesystem, Git, Git LFS, and DVC
inputs.

When an input has a conventional sibling product sidecar such as
`effective-config.prov.json` and its `.sha256` file, input inspection records
the sidecar name and checksum as `product_provenance` metadata. Reproduction
uses this metadata to resolve the input and verify its producing provenance;
dependency-epoch audits use the same reference when comparing product runtime
snapshots.

Use `public_provenance()` before writing records into public outputs. It removes
local-only fields such as repository roots while preserving stable identifiers.

```python
from reprotrail.provenance import get_git_state, get_input_path_state

software = get_git_state(".")
input_state = get_input_path_state("data/input.zarr")
```

History helpers can write compact CF/xarray-style entries:

```python
from reprotrail.provenance import build_cf_history_entry

entry = build_cf_history_entry(["python", "-m", "workflow", "run"])
```
