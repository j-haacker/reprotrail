from __future__ import annotations

import json

import pytest

from reprotrail.product_metadata import (
    ProductMetadataDependencyError,
    match_product_metadata,
    software_license_records,
)
from reprotrail.products import (
    PROVENANCE_ATTR,
    copy_readme_template,
    finalize_product_provenance,
    product_record,
    product_sidecars,
    public_license,
    write_json_with_provenance,
)


def _write_product(project, *, name="sample.dat"):
    data = project / "product" / name
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text("payload\n", encoding="utf-8")
    provenance_path = data.parent / f"{data.stem}.prov.json"
    provenance = {
        "schema_version": "1",
        "status": "completed",
        "history_entry": "2026-01-01T00:00:00+00:00; command",
        "product": product_record(data, provenance_path=provenance_path),
    }
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data, provenance_path


def test_public_license_is_required_and_validated():
    with pytest.raises(ValueError, match="required"):
        public_license(None)
    with pytest.raises(Exception, match="Unknown license"):
        public_license("not valid")
    assert public_license("MIT") == {
        "spdx": "MIT",
        "name": "MIT",
        "url": "https://spdx.org/licenses/MIT.html",
    }


def test_write_json_embeds_public_provenance(tmp_path):
    output = tmp_path / "qc.json"

    write_json_with_provenance(output, {"ok": True}, provenance={"history_entry": "entry"})

    payload = json.loads(output.read_text())
    assert PROVENANCE_ATTR in payload
    assert "history_entry" not in payload[PROVENANCE_ATTR]


def test_product_metadata_matching_rejects_overlaps(tmp_path):
    (tmp_path / "reprotrail.products.toml").write_text(
        """
[[products]]
output = "product/*.dat"
license = "MIT"

[[products]]
output = "product/sample.dat"
license = "CC-BY-4.0"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Multiple product metadata"):
        match_product_metadata(tmp_path / "product" / "sample.dat", tmp_path)


def test_finalize_product_provenance_writes_license_readme_and_rocrate(tmp_path, monkeypatch):
    monkeypatch.setattr("reprotrail.product_metadata.pixi_package_license_records", lambda *_args: [])
    data, provenance_path = _write_product(tmp_path)
    (tmp_path / "reprotrail.products.toml").write_text(
        """
[[products]]
output = "product/sample.dat"
license = "CC-BY-4.0"

[[products.inputs]]
name = "Observed dataset"
producer = "Climate Center"

[[products.inputs]]
name = "Marginal lookup"
producer = "Lookup Producer"
marginal = true

[[products.software]]
name = "workflow-lib"
kind = "package"
license = "MIT"
""",
        encoding="utf-8",
    )

    digest = finalize_product_provenance(provenance_path, project_root=tmp_path)

    sidecars = product_sidecars(data)
    payload = json.loads(provenance_path.read_text(encoding="utf-8"))
    rocrate = json.loads(sidecars.ro_crate.read_text(encoding="utf-8"))
    readme = sidecars.readme.read_text(encoding="utf-8")
    assert digest
    assert payload["license"] == {
        "spdx": "CC-BY-4.0",
        "name": "CC-BY-4.0",
        "url": "https://spdx.org/licenses/CC-BY-4.0.html",
    }
    assert sidecars.license.exists()
    assert sidecars.provenance_sha256.read_text().startswith(f"{digest}  sample.prov.json")
    assert "Observed dataset" in readme
    assert "Marginal lookup" not in readme
    assert "input licenses are unknown for: Observed dataset" in readme
    assert "Marginal lookup" in json.dumps(rocrate)
    assert "workflow-lib" in json.dumps(rocrate)


def test_finalize_without_matching_license_skips_license_file_and_warns(tmp_path, monkeypatch):
    monkeypatch.setattr("reprotrail.product_metadata.pixi_package_license_records", lambda *_args: [])
    data, provenance_path = _write_product(tmp_path)

    finalize_product_provenance(provenance_path, project_root=tmp_path)

    sidecars = product_sidecars(data)
    payload = json.loads(provenance_path.read_text(encoding="utf-8"))
    readme = sidecars.readme.read_text(encoding="utf-8")
    assert "license" not in payload
    assert not sidecars.license.exists()
    assert sidecars.ro_crate.exists()
    assert "No product license was selected" in readme


def test_copy_readme_template_requires_force_for_existing_file(tmp_path):
    output = tmp_path / "README.md.template"

    copy_readme_template(output)

    assert "${license_section}" in output.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        copy_readme_template(output)
    copy_readme_template(output, force=True)


def test_finalize_allows_partial_metadata_when_product_tools_are_missing(tmp_path, monkeypatch):
    data, provenance_path = _write_product(tmp_path)
    (tmp_path / "reprotrail.products.toml").write_text(
        """
[[products]]
output = "product/sample.dat"
license = "MIT"
""",
        encoding="utf-8",
    )

    def missing_tools():
        raise ProductMetadataDependencyError("missing product tools")

    monkeypatch.setattr("reprotrail.products.require_product_metadata_tools", missing_tools)

    with pytest.raises(ProductMetadataDependencyError):
        finalize_product_provenance(provenance_path, project_root=tmp_path)

    finalize_product_provenance(provenance_path, project_root=tmp_path, allow_partial_metadata=True)

    sidecars = product_sidecars(data)
    readme = sidecars.readme.read_text(encoding="utf-8")
    assert sidecars.provenance_sha256.exists()
    assert not sidecars.ro_crate.exists()
    assert "partial product metadata" in readme


def test_software_license_overrides_win_over_pixi_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "reprotrail.product_metadata.pixi_package_license_records",
        lambda *_args: [
            {
                "name": "pkg",
                "version": "1",
                "kind": "pypi",
                "license": "Apache-2.0",
                "license_family": "Apache",
            }
        ],
    )
    (tmp_path / "reprotrail.products.toml").write_text(
        """
[[products]]
output = "product/sample.dat"
license = "MIT"

[[products.software]]
name = "pkg"
kind = "package"
license = "MIT"
""",
        encoding="utf-8",
    )
    metadata = match_product_metadata(tmp_path / "product" / "sample.dat", tmp_path)

    records, warnings = software_license_records(
        project_root=tmp_path,
        pixi_environment=None,
        overrides=metadata.software,
    )

    assert warnings == []
    assert records[0]["license"] == "MIT"
    assert records[0]["license_source"] == "manual"
    assert records[0]["overrides_discovered_license"] is True
