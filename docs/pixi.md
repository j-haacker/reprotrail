# Pixi

## Git dependency freshness

`reprotrail pixi check-git-freshness` checks whether selected Git-backed Pixi
dependencies would move if the lockfile were refreshed. It runs Pixi in dry-run
mode and reports only selected Git/source movements; ordinary registry updates
and unselected package changes do not make the check stale.

```bash
reprotrail pixi check-git-freshness \
  --env downscale \
  --package c4v-utils \
  --package reprotrail \
  --package snippets \
  --package xesmf \
  --package xsdba
```

The command exits with `0` when selected Git sources are fresh, `1` when one or
more selected Git sources would move, and `2` when the Pixi dry run or
normalization fails. It does not update `pixi.lock` or install/update the
environment.

Use `--manifest-path` to point at a specific workspace directory,
`pyproject.toml`, or `pixi.toml`. Without it, reprotrail uses the current
working directory as the Pixi manifest path.

Machine-readable output is available with `--json`:

```bash
reprotrail pixi check-git-freshness \
  --env downscale \
  --package c4v-utils \
  --json
```

Fresh output has an empty `packages` list:

```json
{
  "checked_packages": ["c4v-utils"],
  "environment": "downscale",
  "packages": [],
  "status": "fresh"
}
```

When a selected Git source would move, `packages` contains normalized source
objects with Git URL, commit, and requested revision when Pixi exposes them.
This check is separate from dependency epochs: freshness asks whether moving Git
refs have advanced, while epochs ask whether the locked runtime has been
accepted for a run.
