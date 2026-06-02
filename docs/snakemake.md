# Snakemake Usage

V1 has no Snakemake runtime dependency. Call `reprotrail run` from Snakemake
rules as a normal wrapper command.

```python
shell:
    "reprotrail run "
    "--log {log} "
    "--provenance-json {output.prov} "
    "--product-output {output.data} "
    "-- python -m my_project.step --output {output.data}"
```

Keep project-specific rule expansion, resources, and input selection in the
workflow. Keep generic provenance, Pixi environment capture, dependency epochs,
and reproduction behavior in `reprotrail`.
