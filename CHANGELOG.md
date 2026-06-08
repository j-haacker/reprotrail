# Changelog

## Unreleased

### Added

- Start tracking release notes.
- Add explicit per-product license selection through project-root `reprotrail.products.toml` entries matched by output path.
- Add SPDX product license validation, short provenance license summaries, generated `LICENSE.md` notices, and no-default-license warnings.
- Add RO-Crate sidecar generation with input, attribution, software, and license evidence.
- Add bundled README template rendering and `reprotrail template readme --output PATH`.
- Add Pixi dependency license discovery, manual input/software license overrides, CC-family unknown-input-license warnings, and `--allow-partial-metadata`.
- Add GitHub Actions workflows for CI checks and GitHub Pages documentation publishing.

### Changed

- Reject legacy `[tool.reprotrail.license]` so product licenses are selected per product instead of project-wide.
- Track `pixi.lock` and declare RO-Crate/SPDX tooling for product packaging.
- Advertise Python 3.13 and 3.14 support in package classifiers.

### Documentation

- Document `reprotrail.products.toml`, README template export, RO-Crate sidecars, partial metadata mode, and migration away from `[tool.reprotrail.license]`.
