# c4v-jan Migration Plan

1. Add `reprotrail` to the Pixi local and remote utility dependency features.
2. Replace `workflow/scripts/run_with_monitoring.py` with calls to
   `reprotrail run` from the Snakefile.
3. Replace `workflow/scripts/dependency_guard.py check/audit` invocations with
   `reprotrail epoch check/audit`.
4. Move C4V-specific settings into `[tool.reprotrail]`: repos, product root
   markers, package summary, environment variable whitelist, Pixi environment,
   and license.
5. Keep Snakemake rule structure, output paths, resource settings, and C4V
   config/catalog handling in `c4v-jan`.
6. Regenerate smoke dry-runs and verify provenance sidecars include v1
   `reprotrail` fields.
7. Run `pytest`, smoke dry-run, and one local smoke execution.

Acceptance criteria:

- Workflow scripts no longer contain generic Pixi dependency parsing, dirty
  patch capture, dependency epoch logic, or reproduction code.
- Snakemake still fails before expensive work when dirty/editable policy is
  violated.
- Product provenance includes dependency snapshots and final sidecars.
