"""Product sidecar, checksum, and pointer-attribute helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._json import read_json, write_json
from ._paths import sha256_file
from .provenance import public_provenance

PROVENANCE_ATTR = "processing_provenance"
PROVENANCE_FILE_ATTR = "provenance_file"
PROVENANCE_SHA256_ATTR = "provenance_sha256"
PROVENANCE_SCHEMA_ATTR = "provenance_schema_version"


@dataclass(frozen=True)
class ProductSidecars:
    """Paths that travel with one durable data product."""

    data: Path
    package: Path
    stem: str
    readme: Path
    license: Path
    provenance: Path
    provenance_sha256: Path


def product_sidecars(data_path: str | Path) -> ProductSidecars:
    """Return sidecar paths for a durable data product."""

    data = Path(data_path)
    stem = data.stem
    return ProductSidecars(
        data=data,
        package=data.parent,
        stem=stem,
        readme=data.parent / "README.md",
        license=data.parent / "LICENSE.md",
        provenance=data.parent / f"{stem}.prov.json",
        provenance_sha256=data.parent / f"{stem}.prov.json.sha256",
    )


def write_sha256_file(path: str | Path, *, digest: str, filename: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"{digest}  {filename}\n", encoding="utf-8")


def product_record(
    data_path: str | Path,
    *,
    provenance_path: str | Path | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build generic product metadata for a provenance record."""

    data_path = Path(data_path)
    sidecars = product_sidecars(data_path)
    provenance_file = Path(provenance_path).name if provenance_path else sidecars.provenance.name
    record: dict[str, Any] = {
        "data": data_path.name,
        "format": "zarr" if data_path.suffix == ".zarr" else data_path.suffix.removeprefix("."),
        "package": ".",
        "provenance_file": provenance_file,
        "provenance_sha256_file": f"{provenance_file}.sha256"
        if not provenance_file.endswith(".sha256")
        else provenance_file,
        "readme_file": sidecars.readme.name,
        "license_file": sidecars.license.name,
    }
    if metadata:
        record.update({str(key): value for key, value in metadata.items()})
    return {key: value for key, value in record.items() if value not in (None, "")}


def public_license(license_payload: Mapping[str, Any] | None) -> dict[str, str]:
    """Validate and normalize required product license metadata."""

    if not license_payload:
        raise ValueError(
            "Product license metadata is required. Configure [tool.reprotrail.license] "
            "or pass license metadata before finalizing product sidecars."
        )
    normalized = {str(key): str(value) for key, value in license_payload.items()}
    required = {"spdx", "name", "url"}
    missing = sorted(required - set(normalized))
    if missing:
        raise ValueError(f"Product license metadata is missing: {', '.join(missing)}")
    return normalized


def _readme_text(record: Mapping[str, Any], digest: str, license_payload: Mapping[str, str]) -> str:
    product = record.get("product") or {}
    provenance_file = product.get("provenance_file") or "product.prov.json"
    checksum_file = product.get("provenance_sha256_file") or f"{provenance_file}.sha256"
    data = product.get("data") or "product"
    schema = record.get("schema_version") or record.get("provenance_schema_version") or "1"
    return (
        f"# {data}\n\n"
        "This package contains one data product with reproducibility sidecars.\n\n"
        "## Files\n\n"
        f"- `{data}`: data product\n"
        f"- `{provenance_file}`: product provenance\n"
        f"- `{checksum_file}`: SHA-256 checksum for `{provenance_file}`\n"
        "- `LICENSE.md`: license notice\n\n"
        "## Provenance\n\n"
        f"- Provenance schema: `{schema}`\n"
        f"- Provenance SHA-256: `{digest}`\n\n"
        "Verify the provenance sidecar with:\n\n"
        "```bash\n"
        f"sha256sum -c {checksum_file}\n"
        "```\n\n"
        "The provenance sidecar records the command, software Git states, "
        "runtime environment, and input data states captured for this product.\n\n"
        "## License\n\n"
        f"This product is distributed under {license_payload['name']} "
        f"(`{license_payload['spdx']}`).\n"
    )


def _license_text(license_payload: Mapping[str, str]) -> str:
    return (
        f"SPDX-License-Identifier: {license_payload['spdx']}\n\n"
        f"{license_payload['name']}\n\n"
        f"{license_payload['url']}\n\n"
        "When using or redistributing this product, preserve the provenance "
        "files included in this package.\n"
    )


def write_json_with_provenance(
    path: str | Path,
    payload: dict[str, Any],
    *,
    provenance: dict[str, Any] | None = None,
) -> None:
    """Write JSON metadata, embedding public provenance when supplied."""

    if provenance is not None:
        payload = {**payload, PROVENANCE_ATTR: public_provenance(provenance)}
    write_json(path, payload)


def stamp_dataset_provenance(obj: Any, provenance: dict[str, Any] | None) -> Any:
    """Stamp lightweight provenance pointer attrs on an xarray-like object."""

    if provenance is None:
        return obj
    out = obj.copy()
    product = provenance.get("product") or {}
    if provenance.get("history_entry"):
        out.attrs["history"] = provenance["history_entry"]
    if product.get("provenance_file"):
        out.attrs[PROVENANCE_FILE_ATTR] = product["provenance_file"]
    out.attrs[PROVENANCE_SCHEMA_ATTR] = provenance.get("schema_version", "1")
    out.attrs.pop(PROVENANCE_ATTR, None)
    return out


def _stamp_zarr_pointer_attrs(data_path: Path, record: dict[str, Any], digest: str) -> None:
    try:
        import zarr
    except ImportError as err:  # pragma: no cover - optional dependency
        raise RuntimeError("Install reprotrail[products] to stamp Zarr outputs.") from err

    product = record.get("product") or {}
    group = zarr.open_group(str(data_path), mode="a")
    attrs = dict(group.attrs)
    attrs.pop(PROVENANCE_ATTR, None)
    attrs.update(
        {
            "history": record.get("history_entry", attrs.get("history", "")),
            PROVENANCE_FILE_ATTR: product.get(
                "provenance_file", product_sidecars(data_path).provenance.name
            ),
            PROVENANCE_SHA256_ATTR: digest,
            PROVENANCE_SCHEMA_ATTR: record.get("schema_version", "1"),
        }
    )
    group.attrs.clear()
    group.attrs.update(attrs)


def _stamp_netcdf_pointer_attrs(data_path: Path, record: dict[str, Any], digest: str) -> None:
    try:
        import xarray as xr
    except ImportError as err:  # pragma: no cover - optional dependency
        raise RuntimeError("Install reprotrail[products] to stamp NetCDF outputs.") from err

    product = record.get("product") or {}
    with xr.open_dataset(data_path) as source:
        ds = source.load()
    ds.attrs.pop(PROVENANCE_ATTR, None)
    ds.attrs.update(
        {
            "history": record.get("history_entry", ds.attrs.get("history", "")),
            PROVENANCE_FILE_ATTR: product.get(
                "provenance_file", product_sidecars(data_path).provenance.name
            ),
            PROVENANCE_SHA256_ATTR: digest,
            PROVENANCE_SCHEMA_ATTR: record.get("schema_version", "1"),
        }
    )
    tmp_path = data_path.with_name(f".{data_path.name}.tmp")
    try:
        ds.to_netcdf(tmp_path)
        tmp_path.replace(data_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def finalize_product_provenance(
    provenance_path: str | Path,
    *,
    license: Mapping[str, Any] | None = None,
    stamp: bool = True,
) -> str | None:
    """Finalize a product sidecar checksum and lightweight product attrs."""

    path = Path(provenance_path)
    if not path.exists():
        return None
    record = read_json(path)
    product = record.get("product") or {}
    write_json(path, record)
    digest = sha256_file(path)
    if not product.get("data"):
        write_sha256_file(path.with_suffix(f"{path.suffix}.sha256"), digest=digest, filename=path.name)
        return digest

    data_path = path.parent / str(product["data"])
    sidecars = product_sidecars(data_path)
    checksum_file = product.get("provenance_sha256_file") or sidecars.provenance_sha256.name
    checksum_path = path.parent / str(checksum_file)
    write_sha256_file(checksum_path, digest=digest, filename=path.name)

    if data_path.exists() and stamp:
        if data_path.suffix == ".zarr":
            _stamp_zarr_pointer_attrs(data_path, record, digest)
        elif data_path.suffix == ".nc":
            _stamp_netcdf_pointer_attrs(data_path, record, digest)

    license_payload = public_license(license or record.get("license"))
    readme_file = product.get("readme_file") or sidecars.readme.name
    license_file = product.get("license_file") or sidecars.license.name
    (path.parent / str(readme_file)).write_text(
        _readme_text(record, digest, license_payload),
        encoding="utf-8",
    )
    (path.parent / str(license_file)).write_text(
        _license_text(license_payload),
        encoding="utf-8",
    )
    return digest
