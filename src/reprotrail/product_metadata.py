"""Product license, attribution, and packaging metadata helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

try:  # pragma: no cover - Python 3.11+ path is expected.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from .pixi import pixi_package_license_records

PRODUCT_INDEX_FILE = "reprotrail.products.toml"
RO_CRATE_FILE = "ro-crate-metadata.json"


class ProductMetadataError(ValueError):
    """Raised when product metadata cannot be resolved safely."""


class ProductMetadataDependencyError(RuntimeError):
    """Raised when optional product metadata dependencies are unavailable."""


@dataclass(frozen=True)
class ProductInput:
    """Attribution and license metadata for one product input."""

    name: str | None = None
    producer: str | None = None
    path: str | None = None
    license: str | None = None
    url: str | None = None
    marginal: bool = False


@dataclass(frozen=True)
class SoftwareLicenseOverride:
    """Manual license metadata for one software package or repository."""

    name: str
    kind: str = "package"
    license: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class ProductMetadata:
    """Metadata selected for one product output."""

    output: str
    license: str | None = None
    readme_template: str | None = None
    inputs: tuple[ProductInput, ...] = ()
    software: tuple[SoftwareLicenseOverride, ...] = ()
    source: Path | None = None


def _string_value(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return str(value) if value not in (None, "") else None


def _input_from_mapping(data: Mapping[str, Any]) -> ProductInput:
    return ProductInput(
        name=_string_value(data, "name"),
        producer=_string_value(data, "producer"),
        path=_string_value(data, "path"),
        license=_string_value(data, "license"),
        url=_string_value(data, "url"),
        marginal=bool(data.get("marginal", False)),
    )


def _software_from_mapping(data: Mapping[str, Any]) -> SoftwareLicenseOverride:
    name = _string_value(data, "name")
    if not name:
        raise ProductMetadataError("Product software license overrides require `name`.")
    return SoftwareLicenseOverride(
        name=name,
        kind=_string_value(data, "kind") or "package",
        license=_string_value(data, "license"),
        url=_string_value(data, "url"),
    )


def _product_from_mapping(data: Mapping[str, Any], source: Path) -> ProductMetadata:
    output = _string_value(data, "output")
    if not output:
        raise ProductMetadataError("Product metadata entries require `output`.")
    inputs = tuple(_input_from_mapping(item) for item in data.get("inputs", []) if isinstance(item, Mapping))
    software = tuple(_software_from_mapping(item) for item in data.get("software", []) if isinstance(item, Mapping))
    return ProductMetadata(
        output=output,
        license=_string_value(data, "license"),
        readme_template=_string_value(data, "readme_template"),
        inputs=inputs,
        software=software,
        source=source,
    )


def load_product_index(
    project_root: str | Path,
    *,
    metadata_file: str = PRODUCT_INDEX_FILE,
) -> tuple[ProductMetadata, ...]:
    """Load project-root product metadata entries if the index exists."""

    path = Path(project_root) / metadata_file
    if not path.exists():
        return ()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    products = data.get("products", [])
    if not isinstance(products, list):
        raise ProductMetadataError("`reprotrail.products.toml` must use `[[products]]` entries.")
    return tuple(_product_from_mapping(item, path) for item in products if isinstance(item, Mapping))


def product_match_value(path: str | Path, project_root: str | Path) -> str:
    """Return the project-relative path string used for product metadata matching."""

    root = Path(project_root).resolve()
    value = Path(path)
    absolute = value.resolve() if value.is_absolute() else (root / value).resolve()
    try:
        return absolute.relative_to(root).as_posix()
    except ValueError:
        return absolute.as_posix()


def match_product_metadata(
    product_output: str | Path,
    project_root: str | Path,
    *,
    metadata_file: str = PRODUCT_INDEX_FILE,
) -> ProductMetadata | None:
    """Return the single product metadata entry matching one product output."""

    value = product_match_value(product_output, project_root)
    matches = [
        item
        for item in load_product_index(project_root, metadata_file=metadata_file)
        if fnmatchcase(value, item.output)
    ]
    if len(matches) > 1:
        patterns = ", ".join(item.output for item in matches)
        raise ProductMetadataError(f"Multiple product metadata entries match {value}: {patterns}")
    return matches[0] if matches else None


def require_product_metadata_tools() -> None:
    """Fail clearly when product license/RO-Crate dependencies are unavailable."""

    try:
        from rocrate.rocrate import ROCrate  # noqa: F401
        from spdx_tools.common.spdx_licensing import spdx_licensing  # noqa: F401
    except ImportError as err:  # pragma: no cover - exercised by monkeypatching
        raise ProductMetadataDependencyError(
            "Install reprotrail[products] for RO-Crate/SPDX product metadata support."
        ) from err


def normalize_spdx_expression(expression: str) -> str:
    """Validate and normalize an SPDX expression using the SPDX toolchain."""

    require_product_metadata_tools()
    from spdx_tools.common.spdx_licensing import spdx_licensing

    return spdx_licensing.parse(expression, validate=True, strict=True).render()


def spdx_expression_symbols(expression: str) -> list[str]:
    """Return SPDX license symbols from a validated expression."""

    require_product_metadata_tools()
    from spdx_tools.common.spdx_licensing import spdx_licensing

    parsed = spdx_licensing.parse(expression, validate=True, strict=True)
    return [str(symbol) for symbol in parsed.symbols]


def spdx_url(expression: str) -> str:
    symbols = spdx_expression_symbols(expression)
    return f"https://spdx.org/licenses/{symbols[0]}.html" if len(symbols) == 1 else "https://spdx.org/licenses/"


def product_license_summary(value: str | Mapping[str, Any] | None) -> dict[str, str] | None:
    """Return the short public license summary stored in provenance."""

    if not value:
        return None
    if isinstance(value, Mapping):
        expression = _string_value(value, "spdx") or _string_value(value, "license")
        if not expression:
            raise ProductMetadataError("Product license metadata requires an SPDX expression.")
        normalized = normalize_spdx_expression(expression)
        return {
            "spdx": normalized,
            "name": _string_value(value, "name") or normalized,
            "url": _string_value(value, "url") or spdx_url(normalized),
        }
    normalized = normalize_spdx_expression(str(value))
    return {"spdx": normalized, "name": normalized, "url": spdx_url(normalized)}


def is_cc_family_license(license_summary: Mapping[str, str] | None) -> bool:
    if not license_summary:
        return False
    return any(symbol.startswith("CC-") for symbol in spdx_expression_symbols(str(license_summary["spdx"])))


def _license_validation(value: str | None) -> dict[str, Any]:
    if not value:
        return {"status": "unknown"}
    try:
        normalized = normalize_spdx_expression(value)
    except Exception as err:
        return {"status": "raw", "license": value, "spdx_valid": False, "validation_error": str(err)}
    return {"status": "known", "license": normalized, "spdx_valid": True}


def _read_adjacent_license_file(path: Path) -> str | None:
    license_file = path.parent / "LICENSE.md"
    if not license_file.exists():
        return None
    for line in license_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("SPDX-License-Identifier:"):
            return line.split(":", 1)[1].strip()
    return None


def _read_adjacent_provenance_license(path: Path) -> str | None:
    candidates = sorted(path.parent.glob("*.prov.json"))
    for candidate in candidates:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        license_payload = data.get("license") or {}
        if isinstance(license_payload, Mapping) and license_payload.get("spdx"):
            return str(license_payload["spdx"])
    return None


def _read_adjacent_ro_crate_license(path: Path) -> str | None:
    crate = path.parent / RO_CRATE_FILE
    if not crate.exists():
        return None
    try:
        data = json.loads(crate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for entity in data.get("@graph", []):
        if not isinstance(entity, Mapping):
            continue
        if entity.get("@id") in {path.name, "./"} and entity.get("license"):
            return str(entity["license"])
    return None


def resolve_input_path(value: str, project_root: str | Path, package_dir: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    project_candidate = Path(project_root) / path
    if project_candidate.exists():
        return project_candidate
    return Path(package_dir) / path


def input_license_records(
    inputs: tuple[ProductInput, ...],
    *,
    project_root: str | Path,
    package_dir: str | Path,
) -> list[dict[str, Any]]:
    """Build attribution/license evidence records for product-index inputs."""

    records = []
    for index, item in enumerate(inputs, start=1):
        if not item.marginal and (not item.name or not item.producer):
            raise ProductMetadataError("Non-marginal product inputs require `name` and `producer`.")
        raw_license = item.license
        source = "manual" if raw_license else "unknown"
        resolved_path = None
        if raw_license is None and item.path:
            resolved_path = resolve_input_path(item.path, project_root, package_dir)
            for candidate_source, reader in (
                ("ro-crate", _read_adjacent_ro_crate_license),
                ("provenance", _read_adjacent_provenance_license),
                ("license-file", _read_adjacent_license_file),
            ):
                raw_license = reader(resolved_path)
                if raw_license:
                    source = candidate_source
                    break
        validation = _license_validation(raw_license)
        record: dict[str, Any] = {
            "id": f"input-{index}",
            "name": item.name,
            "producer": item.producer,
            "path": item.path,
            "url": item.url,
            "marginal": item.marginal,
            "license_source": source,
            **validation,
        }
        if resolved_path is not None:
            record["resolved_path"] = str(resolved_path)
        records.append({key: value for key, value in record.items() if value not in (None, "")})
    return records


def _project_license_record(project_root: Path) -> dict[str, Any] | None:
    pyproject = project_root / "pyproject.toml"
    if not pyproject.exists():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    project = data.get("project") or {}
    name = project.get("name")
    license_value = project.get("license")
    if not name or not license_value:
        return None
    if isinstance(license_value, Mapping):
        license_value = license_value.get("text") or license_value.get("file")
    validation = _license_validation(str(license_value))
    return {
        "name": str(name),
        "kind": "project",
        "version": project.get("version"),
        "license_source": "pyproject",
        **validation,
    }


def software_license_records(
    *,
    project_root: str | Path,
    pixi_environment: str | None,
    overrides: tuple[SoftwareLicenseOverride, ...],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build software/dependency license evidence records."""

    root = Path(project_root)
    warnings = []
    records: dict[str, dict[str, Any]] = {}
    try:
        for package in pixi_package_license_records(root, pixi_environment):
            name = str(package.get("name") or "")
            if not name:
                continue
            validation = _license_validation(package.get("license"))
            records[name] = {
                "name": name,
                "kind": package.get("kind") or "package",
                "version": package.get("version"),
                "license_family": package.get("license_family"),
                "license_source": "pixi-list",
                **validation,
            }
    except Exception as err:
        warnings.append(f"Software license discovery failed: {err}")

    project_record = _project_license_record(root)
    if project_record and not records.get(str(project_record["name"]), {}).get("license"):
        records[str(project_record["name"])] = project_record

    for override in overrides:
        validation = _license_validation(override.license)
        existing = records.get(override.name, {})
        records[override.name] = {
            **existing,
            "name": override.name,
            "kind": override.kind,
            "url": override.url,
            "license_source": "manual",
            "overrides_discovered_license": bool(existing.get("license") and override.license),
            **validation,
        }
    return [records[key] for key in sorted(records)], warnings
