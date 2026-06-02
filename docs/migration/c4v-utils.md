# c4v-utils Migration Plan

1. Add `reprotrail` as a dependency for the downscaling extra.
2. Replace local provenance serialization helpers with
   `reprotrail.provenance.public_provenance`, `get_input_path_state`, and product
   sidecar helpers.
3. Replace `finalize_product_provenance`, product README/LICENSE rendering, and
   reproduction CLI delegation with `reprotrail.products` and
   `reprotrail.reproduce`.
4. Keep downscaling-specific CLI commands, scientific transforms, data
   selection, QA reports, and metrics logic in `c4v-utils`.
5. Preserve explicit input-path collection in each downscaling command; pass
   those paths into small C4V adapter code that writes v1 `reprotrail`
   provenance records.
6. Update parser tests so provenance flags remain accepted but delegate generic
   behavior to `reprotrail`.
7. Run `pytest` for c4v-utils and one smoke command through c4v-jan.

Acceptance criteria:

- c4v-utils no longer depends on `snippets` for provenance/reproduction.
- Product sidecar naming and pointer attrs remain stable for C4V products.
- Downscaling tests still verify explicit provenance inputs per command.
