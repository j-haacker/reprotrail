# Dependency Epochs

Dependency epochs guard a run root against unacknowledged runtime changes.
Snapshots include Pixi lockfile hash, active Pixi environment, configured
package versions, platform identity, and external editable dependency Git state.

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

Audit product sidecars:

```bash
reprotrail epoch audit --run-root results/run --output results/run/qc/epochs.json
```
