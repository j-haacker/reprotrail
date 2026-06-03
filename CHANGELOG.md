# Changelog

## Unreleased

- Start tracking release notes.
- Planned license-aware product packaging:
  - Select product licenses explicitly per product output.
  - Generate product README, license notice, and RO-Crate sidecars without defaulting to a license.
  - Record short product license summaries in provenance when a license is selected.
- Add bundled product README template scaffolding and declare RO-Crate/SPDX tooling for product packaging.
- Add product metadata index matching, SPDX product license validation, short provenance license summaries, RO-Crate sidecar generation, README attribution/warning rendering, Pixi dependency license discovery, and partial-metadata finalization support.
- Document `reprotrail.products.toml`, README template export, RO-Crate sidecars, and migration away from `[tool.reprotrail.license]`.
