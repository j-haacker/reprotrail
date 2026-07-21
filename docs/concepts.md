# Concepts and terminology

This page defines the terms used by reprotrail. They describe different parts
of a recorded run rather than additional services that must all be deployed.

(concept-provenance-record)=
## Provenance record

A **provenance record** is the versioned JSON document written for one command.
It describes what ran, when it ran, its completion status, trusted software
state, selected inputs, and available runtime information. Provenance helps
answer how a result was produced; it does not by itself guarantee that the
command is deterministic or that external data remains available.

(concept-product)=
## Product and sidecar

A **product** is the durable file or directory produced by a run, such as a
NetCDF file or Zarr store. A **sidecar** is a separate metadata file stored next
to a product. Reprotrail sidecars can include a provenance record and checksum,
README, license notice, and `ro-crate-metadata.json`.

(concept-runtime-snapshot)=
## Runtime snapshot

A **runtime snapshot** is a normalized description of the environment used by
a command. With Pixi, it includes the lockfile hash, selected environment,
package versions and source information, platform identity, and relevant
editable dependency Git state. It is evidence about an environment, not a copy
of every installed file.

(concept-dependency-epoch)=
## Dependency epoch

A **dependency epoch** is an accepted runtime snapshot for a run root. When the
snapshot changes, `reprotrail epoch check` requires an explicit acceptance
reason before work continues. The term “epoch” is convenient shorthand for a
period in which the accepted dependencies are unchanged; it is not a calendar
or package-manager concept.

(concept-run-root)=
## Run root

The **run root** is the directory that groups one logical workflow run and its
provenance contract. Product path markers can help infer it. For example, if
`products` is configured as a marker, the record
`results/run/products/a/a.prov.json` belongs to `results/run`.

(concept-repositories)=
## Trusted and diagnostic repositories

**Trusted runtime repositories** are source checkouts that can affect the
executed code: the project repository and active external editable or path
dependencies. Dirty trusted repositories block execution by default.

**Diagnostic repositories** are additional configured checkouts that are useful
to observe but are not active runtime sources. Their state is recorded under
`configured_repos`; it does not satisfy runtime provenance and does not block a
run when dirty.

(concept-pixi)=
## Pixi

[Pixi](https://pixi.sh/) is a project and environment manager that creates a
cross-platform lockfile. Reprotrail reads Pixi state and invokes the external
`pixi` executable for environment inspection, freshness checks, and
reproduction. Pixi is not installed by the PyPI package.

(concept-rocrate)=
## RO-Crate

[RO-Crate](https://www.researchobject.org/ro-crate/) is a JSON-LD format for
describing research data packages and their relationships. Reprotrail uses it
to connect products with inputs, software, attribution, and license evidence.

(concept-spdx)=
## SPDX

[SPDX](https://spdx.dev/) provides standardized identifiers and expressions for
software and data licenses, such as `MIT` or `CC-BY-4.0`. Reprotrail validates
explicit product license expressions but never chooses a product license for
the user.

(concept-cf-history)=
## CF/xarray history

The Climate and Forecast metadata conventions define a `history` attribute for
recording processing steps. Reprotrail can create compact history lines and
attach them to xarray-like objects. These lines complement the richer JSON
provenance record.

(concept-data-backends)=
## DVC and Git LFS

[DVC](https://dvc.org/) and [Git LFS](https://git-lfs.com/) track data or large
files through small repository metadata. When detected, reprotrail records
their relevant identifiers. It does not upload, download, or guarantee access
to the underlying data.
