# Maintainer release guide

This guide covers repeatable PyPI releases and the subsequent conda-forge
update. End users do not need these steps.

## Prepare a release

1. Choose the new version, update it in `pyproject.toml` and
   `src/reprotrail/__init__.py`, and move the relevant changelog entries from
   `Unreleased` beneath a dated heading for that version.
2. Confirm `main` is clean and synchronized, then run the normal checks:

   ```bash
   uv run --extra dev --extra products pytest
   uv run --extra dev ruff check .
   uv run --extra dev sphinx-build -W -b html docs docs/_build/html
   ```

3. Check that package metadata, runtime metadata, and the changelog agree. The
   privacy check always detects generic machine paths and credential-shaped
   values; it also loads repository-specific terms from the ignored
   `.privacy-filters.local.toml` file when present:

   ```bash
   uv run python scripts/check_release.py
   uv run python scripts/check_privacy.py
   ```

4. Build and inspect the exact artifacts locally:

   ```bash
   uv build
   uv run --with twine twine check dist/*
   uv run python scripts/check_dist.py dist
   ```

5. Run the Publish workflow manually. A manual dispatch rehearses the same
   checks and artifact construction but cannot publish because it is not a tag
   event.

## Tag and publish

Read the version from project metadata and create the matching annotated tag:

```bash
VERSION=$(uv run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
git tag -a "v${VERSION}" -m "reprotrail ${VERSION}"
git push origin "v${VERSION}"
```

The tag-triggered workflow verifies that the tag and package version agree,
builds the distributions once, and publishes that same artifact set through
the protected environment. Afterward, verify the PyPI metadata, files, hashes,
project links, and installation in a clean environment.

PyPI versions and files are immutable. If publishing fails before upload, fix
the workflow and rerun the tag job. If any file reached PyPI, never delete it
and reuse that version; correct the issue, add a changelog entry, bump the
package and runtime versions together, and publish a new patch release.

## Update conda-forge

Update conda-forge only after the PyPI sdist is available. Use the released
version, source URL, and authoritative SHA256 in the feedstock recipe. Keep its
Python requirement and full runtime dependency set aligned with
`pyproject.toml`; the conda package intentionally installs the complete
Pixi-first workflow rather than reproducing PyPI extras.

Build the recipe through the official conda-forge workflow and verify at least
the package import, runtime version, CLI help, license file, and dependency
consistency. The feedstock remains the source of truth for the recipe; this
repository should not carry a duplicate copy.
