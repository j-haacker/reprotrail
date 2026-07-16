# Maintainer release guide

This guide covers the `0.1.0` PyPI release and the subsequent conda-forge
submission. End users do not need these steps.

## One-time PyPI setup

Create a pending Trusted Publisher for project `reprotrail` on PyPI with:

- owner `j-haacker`;
- repository `reprotrail`;
- workflow `publish.yml`; and
- environment `pypi`.

In the GitHub repository, create a protected `pypi` environment. The publishing
job requests only `id-token: write`; do not create a PyPI API-token secret.

## Release `0.1.0`

1. Confirm `main` is clean and CI and documentation checks pass.
2. Confirm the release version matches `src/reprotrail/__init__.py`,
   `pyproject.toml`, and the dated changelog heading:

   ```bash
   uv run python scripts/check_release.py
   uv run python scripts/check_privacy.py
   ```

   The privacy scan covers tracked files, lockfiles, and generated documentation
   sources. Its narrow identity allowlist permits the intentional author,
   copyright, repository, and documentation identity only where publication
   metadata or maintainer instructions require it.

3. Run the Publish workflow manually. This rehearses all checks and artifact
   construction but cannot publish because the ref is not a tag.
4. Recheck that `https://pypi.org/project/reprotrail/` is unclaimed.
5. Create and push the exact release tag:

   ```bash
   git tag -a v0.1.0 -m "reprotrail 0.1.0"
   git push origin v0.1.0
   ```

6. Approve the protected `pypi` environment if approval is configured. The
   workflow builds once, validates the artifacts, and publishes that same
   artifact set through Trusted Publishing.
7. Verify the PyPI metadata, files, hashes, project links, and installation in a
   clean environment.

PyPI versions and files are immutable. If publishing fails before upload, fix
the workflow and rerun the tag job. If any `0.1.0` file reached PyPI, never
delete and reuse that version; correct the issue, add a new changelog entry,
bump the package/runtime versions together, and publish a new patch release.

## Conda-forge handoff

Submit conda-forge only after the PyPI sdist is available. Download its
authoritative SHA256 from PyPI, replace `<PYPI_SDIST_SHA256>` below, and place
the finalized file at `recipes/reprotrail/recipe.yaml` in a branch of
`conda-forge/staged-recipes`:

```yaml
context:
  version: "0.1.0"

package:
  name: reprotrail
  version: ${{ version }}

source:
  url: https://pypi.org/packages/source/r/reprotrail/reprotrail-${{ version }}.tar.gz
  sha256: <PYPI_SDIST_SHA256>

build:
  noarch: python
  script: python -m pip install . -vv --no-deps --no-build-isolation
  number: 0

requirements:
  host:
    - python
    - pip
    - uv-build >=0.11.0,<0.12.0
  run:
    - python >=3.11
    - pixi
    - rocrate
    - spdx-tools
    - xarray
    - zarr

tests:
  - python:
      imports:
        - reprotrail
      pip_check: true
  - script:
      - python -c "import reprotrail; assert reprotrail.__version__ == '0.1.0'"
      - reprotrail --help

about:
  homepage: https://github.com/j-haacker/reprotrail
  summary: Reusable provenance, runtime snapshot, and reproduction helpers
  description: |
    Reprotrail records software, input, runtime, and product provenance for
    data-processing runs and prepares reproduction workspaces.
  license: MIT
  license_file: LICENSE
  documentation: https://j-haacker.github.io/reprotrail/
  repository: https://github.com/j-haacker/reprotrail

extra:
  recipe-maintainers:
    - j-haacker
```

The conda package intentionally installs the full Pixi-first workflow rather
than reproducing PyPI extras. Before opening the pull request, run the official
staged-recipes local build for the recipe and confirm the import, version, CLI,
license, and dependency tests pass. Recheck that the conda-forge package name is
still unclaimed, then follow the conda-forge review process. The generated
feedstock becomes the source of truth for future conda recipe updates; do not
maintain a duplicate recipe in this repository.
