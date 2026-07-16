# Dependency Epochs

Dependency epochs guard a run root against unacknowledged runtime changes.
Snapshots include Pixi lockfile hash, active Pixi environment, configured
package versions, sanitized installed package source metadata, platform
identity, and external editable dependency Git state. Source metadata is included
so Git-installed packages can change dependency epochs even when their version
strings do not change.

(cli-epoch-check)=
## `reprotrail epoch check` arguments

Initialize or check a contract:

```bash
reprotrail epoch check --run-root results/run
```

Accept a changed runtime knowingly:

```bash
reprotrail epoch check \
  --run-root results/run \
  --acceptance-reason "validated smoke metrics"
```

| Syntax | Status | Behavior |
| --- | --- | --- |
| `--run-root PATH` | Required | Checks the dependency epoch contract under run-root directory `PATH`. |
| `--acceptance-reason TEXT` | Optional | Accepts an unrecognized dependency snapshot and records `TEXT` as its reason. Without a reason, a changed snapshot fails instead of being accepted. |
| `--dry-run` | Optional | Reports whether reprotrail would initialize or accept a dependency epoch without writing the contract. |
| `--env ENV` | Optional | Uses Pixi environment `ENV` instead of the environment configured for the project. |
| `--json` | Optional | Prints the dependency epoch result as JSON instead of a human-readable summary. |

(cli-epoch-audit)=
## `reprotrail epoch audit` arguments

Audit product sidecars:

```bash
reprotrail epoch audit --run-root results/run --output results/run/qc/epochs.json
```

| Syntax | Status | Behavior |
| --- | --- | --- |
| `--run-root PATH` | Required | Searches run-root directory `PATH` for product provenance records. |
| `--output PATH` | Required | Writes the dependency epoch audit report to `PATH`. |
| `--json` | Optional | Prints the audit report as JSON in addition to writing it to `--output`. |
