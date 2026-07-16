# Changelog

## Unreleased

## 0.1.0 - 2026-07-16

### Added

- Start tracking release notes.
- Add explicit per-product license selection through project-root `reprotrail.products.toml` entries matched by output path.
- Add SPDX product license validation, short provenance license summaries, generated `LICENSE.md` notices, and no-default-license warnings.
- Add RO-Crate sidecar generation with input, attribution, software, and license evidence.
- Add bundled README template rendering and `reprotrail template readme --output PATH`.
- Add Pixi dependency license discovery, manual input/software license overrides, CC-family unknown-input-license warnings, and `--allow-partial-metadata`.
- Add `reprotrail pixi check-git-freshness` for checking whether selected Git-backed Pixi dependencies would move on a dry-run update.
- Add GitHub Actions workflows for CI checks and GitHub Pages documentation publishing.
- Add repeatable `reprotrail run --input PATH` declarations with pre-execution snapshots, child-sidecar merging, and input product provenance verification.

### Changed

- Reject legacy `[tool.reprotrail.license]` so product licenses are selected per product instead of project-wide.
- Track `pixi.lock` and declare RO-Crate/SPDX tooling for product packaging.
- Advertise Python 3.13 and 3.14 support in package classifiers.

### Fixed

- Restore project and editable-dependency repositories from recorded commit hashes without requiring recorded branches
  to remain available on the remote.
- Fix runtime provenance so configured sibling repositories are not recorded as trusted runtime software when the active Pixi environment uses installed package sources instead (#4).

### Documentation

- Add terminal and documentation descriptions for every CLI command and argument, plus a workflow-oriented CLI overview.
- Document `reprotrail.products.toml`, README template export, RO-Crate sidecars, partial metadata mode, and migration away from `[tool.reprotrail.license]`.
- Add PyPI and conda-forge release preparation, artifact validation, and maintainer release documentation.
- Add an end-user quickstart, terminology guide, privacy guidance, and documented `0.x` API stability policy.
