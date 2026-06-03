"""Product sidecar, checksum, and pointer-attribute helpers."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from string import Template
from typing import Any

from ._json import read_json, write_json
from ._paths import sha256_file
from .product_metadata import (
    PRODUCT_INDEX_FILE,
    RO_CRATE_FILE,
    ProductMetadata,
    ProductMetadataDependencyError,
    input_license_records,
    is_cc_family_license,
    match_product_metadata,
    product_license_summary,
    require_product_metadata_tools,
    software_license_records,
)
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
    ro_crate: Path
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
        ro_crate=data.parent / RO_CRATE_FILE,
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
        "ro_crate_file": sidecars.ro_crate.name,
    }
    if metadata:
        record.update({str(key): value for key, value in metadata.items()})
    return {key: value for key, value in record.items() if value not in (None, "")}


def public_license(license_payload: str | Mapping[str, Any] | None) -> dict[str, str]:
    """Validate and normalize required product license metadata."""

    if not license_payload:
        raise ValueError(
            "Product license metadata is required. Configure a matching entry in "
            "reprotrail.products.toml or pass explicit product license metadata before finalizing product sidecars."
        )
    summary = product_license_summary(license_payload)
    if summary is None:
        raise ValueError("Product license metadata is required.")
    return summary


def default_readme_template_text() -> str:
    """Return the bundled product README template."""

    return resources.files("reprotrail.templates").joinpath("product_README.md.template").read_text(encoding="utf-8")


def copy_readme_template(output: str | Path, *, force: bool = False) -> Path:
    """Copy the bundled product README template for project customization."""

    destination = Path(output)
    if destination.exists() and not force:
        raise FileExistsError(f"README template already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with resources.as_file(resources.files("reprotrail.templates").joinpath("product_README.md.template")) as source:
        shutil.copyfile(source, destination)
    return destination


def _template_text(metadata: ProductMetadata | None, project_root: Path) -> str:
    if metadata is None or not metadata.readme_template:
        return default_readme_template_text()
    path = Path(metadata.readme_template)
    if not path.is_absolute():
        path = project_root / path
    return path.read_text(encoding="utf-8")


def _files_section(product: Mapping[str, Any], *, license_payload: Mapping[str, str] | None) -> str:
    data = product.get("data") or "product"
    provenance_file = product.get("provenance_file") or "product.prov.json"
    checksum_file = product.get("provenance_sha256_file") or f"{provenance_file}.sha256"
    ro_crate_file = product.get("ro_crate_file") or RO_CRATE_FILE
    lines = [
        f"- `{data}`: data product",
        f"- `{provenance_file}`: product provenance",
        f"- `{checksum_file}`: SHA-256 checksum for `{provenance_file}`",
        f"- `{ro_crate_file}`: RO-Crate metadata for this product package",
    ]
    if license_payload:
        lines.append("- `LICENSE.md`: license notice")
    return "\n".join(lines)


def _license_section(license_payload: Mapping[str, str] | None) -> str:
    if license_payload:
        return f"This product is distributed under {license_payload['name']} (`{license_payload['spdx']}`)."
    return "No product license was selected. This package does not include a `LICENSE.md` notice."


def _attribution_section(input_records: list[dict[str, Any]]) -> str:
    entries = []
    for item in input_records:
        if item.get("marginal"):
            continue
        parts = [str(item.get("name") or item.get("path") or item["id"])]
        if item.get("producer"):
            parts.append(f"producer: {item['producer']}")
        if item.get("license"):
            parts.append(f"license: {item['license']}")
        else:
            parts.append("license: unknown")
        if item.get("url"):
            parts.append(f"url: {item['url']}")
        entries.append("- " + "; ".join(parts))
    return "\n".join(entries) if entries else "No non-marginal input attribution entries were provided."


def _warnings_section(warnings: list[str]) -> str:
    return "\n".join(f"- {warning}" for warning in warnings) if warnings else "No packaging warnings."


def _readme_text(
    record: Mapping[str, Any],
    digest: str,
    license_payload: Mapping[str, str] | None,
    *,
    input_records: list[dict[str, Any]],
    warnings: list[str],
    metadata: ProductMetadata | None,
    project_root: Path,
) -> str:
    product = record.get("product") or {}
    provenance_file = product.get("provenance_file") or "product.prov.json"
    checksum_file = product.get("provenance_sha256_file") or f"{provenance_file}.sha256"
    data = product.get("data") or "product"
    schema = record.get("schema_version") or record.get("provenance_schema_version") or "1"
    template = Template(_template_text(metadata, project_root))
    return template.safe_substitute(
        data=data,
        provenance_file=provenance_file,
        checksum_file=checksum_file,
        schema=schema,
        digest=digest,
        files_section=_files_section(product, license_payload=license_payload),
        license_section=_license_section(license_payload),
        attribution_section=_attribution_section(input_records),
        warnings_section=_warnings_section(warnings),
    )


def _license_text(license_payload: Mapping[str, str]) -> str:
    return (
        f"SPDX-License-Identifier: {license_payload['spdx']}\n\n"
        f"{license_payload['name']}\n\n"
        f"{license_payload['url']}\n\n"
        "When using or redistributing this product, preserve the provenance "
        "files included in this package.\n"
    )


def _append_record_warnings(record: dict[str, Any], warnings: list[str]) -> None:
    if not warnings:
        return
    existing = list(record.get("warnings") or [])
    for warning in warnings:
        if warning not in existing:
            existing.append(warning)
    record["warnings"] = existing


def _entity_id(value: str) -> str:
    return value.replace("\\", "/").replace(" ", "-").replace(":", "-").replace("#", "-").strip("/") or "unknown"


def _add_package_file(crate: Any, path: Path, *, properties: dict[str, Any] | None = None) -> None:
    if path.exists() and path.is_dir():
        crate.add_dataset(str(path), dest_path=path.name, properties=properties)
    else:
        crate.add_file(str(path), dest_path=path.name, properties=properties)


def _write_ro_crate(
    path: Path,
    *,
    record: dict[str, Any],
    data_path: Path,
    digest: str,
    license_payload: Mapping[str, str] | None,
    input_records: list[dict[str, Any]],
    software_records: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    from rocrate.rocrate import ROCrate

    product = record.get("product") or {}
    crate = ROCrate()
    crate.root_dataset["name"] = product.get("data") or data_path.name
    crate.root_dataset["description"] = "Reprotrail product package metadata."
    crate.root_dataset["provenance_sha256"] = digest
    if license_payload:
        crate.root_dataset["license"] = license_payload["spdx"]
    if warnings:
        crate.root_dataset["reprotrail_packaging_warnings"] = warnings

    data_properties: dict[str, Any] = {"name": product.get("data") or data_path.name}
    if license_payload:
        data_properties["license"] = license_payload["spdx"]
    _add_package_file(crate, data_path, properties=data_properties)

    for filename in (
        product.get("provenance_file"),
        product.get("provenance_sha256_file"),
        product.get("readme_file"),
        product.get("license_file") if license_payload else None,
    ):
        if filename:
            _add_package_file(crate, path.parent / str(filename))

    for item in input_records:
        entity_id = item.get("path") or item.get("url") or item["id"]
        entity: dict[str, Any] = {
            "@id": str(entity_id),
            "@type": "Dataset",
            "name": item.get("name") or str(entity_id),
            "producer": item.get("producer"),
            "license": item.get("license"),
            "url": item.get("url"),
            "marginal": item.get("marginal", False),
            "license_status": item.get("status"),
            "license_source": item.get("license_source"),
            "spdx_valid": item.get("spdx_valid"),
        }
        crate.add_jsonld({key: value for key, value in entity.items() if value not in (None, "")})

    for item in software_records:
        entity: dict[str, Any] = {
            "@id": f"software/{_entity_id(str(item.get('name') or 'unknown'))}",
            "@type": "SoftwareApplication",
            "name": item.get("name"),
            "softwareVersion": item.get("version"),
            "applicationCategory": item.get("kind"),
            "license": item.get("license"),
            "url": item.get("url"),
            "license_family": item.get("license_family"),
            "license_status": item.get("status"),
            "license_source": item.get("license_source"),
            "spdx_valid": item.get("spdx_valid"),
            "overrides_discovered_license": item.get("overrides_discovered_license"),
        }
        crate.add_jsonld({key: value for key, value in entity.items() if value not in (None, "")})

    write_json(path, crate.metadata.generate())


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
            PROVENANCE_FILE_ATTR: product.get("provenance_file", product_sidecars(data_path).provenance.name),
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
            PROVENANCE_FILE_ATTR: product.get("provenance_file", product_sidecars(data_path).provenance.name),
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
    project_root: str | Path | None = None,
    pixi_environment: str | None = None,
    product_metadata_file: str = PRODUCT_INDEX_FILE,
    license: str | Mapping[str, Any] | None = None,
    allow_partial_metadata: bool = False,
    stamp: bool = True,
) -> str | None:
    """Finalize a product sidecar checksum and lightweight product attrs."""

    path = Path(provenance_path)
    if not path.exists():
        return None
    record = read_json(path)
    product = record.get("product") or {}
    if not product.get("data"):
        write_json(path, record)
        digest = sha256_file(path)
        write_sha256_file(path.with_suffix(f"{path.suffix}.sha256"), digest=digest, filename=path.name)
        return digest

    data_path = path.parent / str(product["data"])
    sidecars = product_sidecars(data_path)
    root = Path(project_root).resolve() if project_root is not None else Path.cwd().resolve()
    warnings: list[str] = []
    metadata = match_product_metadata(data_path, root, metadata_file=product_metadata_file)
    selected_license = license if license is not None else (metadata.license if metadata is not None else None)

    tools_available = True
    try:
        require_product_metadata_tools()
    except ProductMetadataDependencyError as err:
        if not allow_partial_metadata:
            raise
        tools_available = False
        warnings.append(f"{err} Wrote partial product metadata because --allow-partial-metadata was set.")

    license_payload = None
    input_records: list[dict[str, Any]] = []
    software_records: list[dict[str, Any]] = []
    if tools_available:
        license_payload = product_license_summary(selected_license)
        if metadata is None and license is None:
            warnings.append(f"No product metadata entry matched {data_path.name}; no product license was selected.")
        elif selected_license is None:
            warnings.append("No product license was selected.")
        input_records = input_license_records(
            metadata.inputs if metadata is not None else (),
            project_root=root,
            package_dir=path.parent,
        )
        software_records, software_warnings = software_license_records(
            project_root=root,
            pixi_environment=pixi_environment,
            overrides=metadata.software if metadata is not None else (),
        )
        warnings.extend(software_warnings)
        if is_cc_family_license(license_payload):
            unknown_inputs = [
                str(item.get("name") or item.get("path") or item["id"])
                for item in input_records
                if not item.get("marginal") and not item.get("license")
            ]
            if unknown_inputs:
                warnings.append(
                    "CC-family product license selected, but input licenses are unknown for: "
                    + ", ".join(unknown_inputs)
                )
    elif selected_license is not None:
        warnings.append("Product license was selected but not validated because product metadata tools are missing.")
    else:
        warnings.append("No product license was selected.")

    if license_payload:
        record["license"] = license_payload
        product["license_file"] = product.get("license_file") or sidecars.license.name
    else:
        record.pop("license", None)
        product.pop("license_file", None)
    product["ro_crate_file"] = product.get("ro_crate_file") or sidecars.ro_crate.name
    record["product"] = product
    _append_record_warnings(record, warnings)
    write_json(path, record)
    digest = sha256_file(path)

    checksum_file = product.get("provenance_sha256_file") or sidecars.provenance_sha256.name
    checksum_path = path.parent / str(checksum_file)
    write_sha256_file(checksum_path, digest=digest, filename=path.name)

    if data_path.exists() and stamp:
        if data_path.suffix == ".zarr":
            _stamp_zarr_pointer_attrs(data_path, record, digest)
        elif data_path.suffix == ".nc":
            _stamp_netcdf_pointer_attrs(data_path, record, digest)

    readme_file = product.get("readme_file") or sidecars.readme.name
    (path.parent / str(readme_file)).write_text(
        _readme_text(
            record,
            digest,
            license_payload,
            input_records=input_records,
            warnings=warnings,
            metadata=metadata,
            project_root=root,
        ),
        encoding="utf-8",
    )
    if license_payload:
        license_file = product.get("license_file") or sidecars.license.name
        (path.parent / str(license_file)).write_text(
            _license_text(license_payload),
            encoding="utf-8",
        )
    if tools_available:
        _write_ro_crate(
            path.parent / str(product.get("ro_crate_file") or sidecars.ro_crate.name),
            record=record,
            data_path=data_path,
            digest=digest,
            license_payload=license_payload,
            input_records=input_records,
            software_records=software_records,
            warnings=warnings,
        )
    return digest
