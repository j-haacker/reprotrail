# snipGit Migration Plan

1. Add `reprotrail` as a dependency or editable sibling dependency.
2. Replace `snippets.provenance` imports with `reprotrail.provenance`.
3. Replace `snippets.reproduce` imports with `reprotrail.reproduce`.
4. Keep unrelated snippet modules in `snippets`; do not migrate debugging,
   monitoring, xarray utilities, or monkeypatch helpers unless separately
   planned.
5. Move or delete duplicated provenance/reproduction tests after confirming the
   corresponding `reprotrail` tests cover the same behavior.
6. Update docs to point provenance/reproduction users to `reprotrail`.
7. Run `pytest` and the Sphinx build for both repos.

Acceptance criteria:

- No `snippets.provenance` or `snippets.reproduce` source imports remain outside
  compatibility shims.
- snipGit docs clearly identify `reprotrail` as the maintained provenance tool.
- Existing snipGit non-provenance tests still pass.
