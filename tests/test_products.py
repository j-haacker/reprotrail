from __future__ import annotations

import json

import pytest

from reprotrail.products import (
    PROVENANCE_ATTR,
    finalize_product_provenance,
    product_record,
    product_sidecars,
    public_license,
    write_json_with_provenance,
)

LICENSE = {
    "spdx": "CC-BY-4.0",
    "name": "Creative Commons Attribution 4.0 International",
    "url": "https://creativecommons.org/licenses/by/4.0/",
}


def test_public_license_is_required():
    with pytest.raises(ValueError, match="required"):
        public_license(None)
    with pytest.raises(ValueError, match="missing"):
        public_license({"spdx": "MIT"})


def test_write_json_embeds_public_provenance(tmp_path):
    output = tmp_path / "qc.json"

    write_json_with_provenance(output, {"ok": True}, provenance={"history_entry": "entry"})

    payload = json.loads(output.read_text())
    assert PROVENANCE_ATTR in payload
    assert "history_entry" not in payload[PROVENANCE_ATTR]


def test_finalize_product_provenance_writes_sidecars(tmp_path):
    data = tmp_path / "product" / "sample.dat"
    data.parent.mkdir()
    data.write_text("payload\n", encoding="utf-8")
    provenance_path = tmp_path / "product" / "sample.prov.json"
    provenance = {
        "schema_version": "1",
        "status": "completed",
        "history_entry": "2026-01-01T00:00:00+00:00; command",
        "product": product_record(data, provenance_path=provenance_path),
        "license": LICENSE,
    }
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")

    digest = finalize_product_provenance(provenance_path)

    sidecars = product_sidecars(data)
    assert digest
    assert sidecars.readme.exists()
    assert sidecars.license.exists()
    assert sidecars.provenance_sha256.read_text().startswith(f"{digest}  sample.prov.json")
    assert "sample.dat" in sidecars.readme.read_text(encoding="utf-8")
