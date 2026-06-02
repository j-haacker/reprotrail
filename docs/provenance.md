# Provenance

`reprotrail.provenance` records software and input state in portable metadata.
It captures Git commit, branch, canonical remote URL, dirty status, optional
dirty diff hash, and compact path state for filesystem, Git, Git LFS, and DVC
inputs.

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
