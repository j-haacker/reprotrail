from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # pragma: no cover - Python 3.11+ path is expected.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


DEFAULT_ENV_VAR_WHITELIST = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "PIXI_ENVIRONMENT_NAME",
    "SLURM_JOB_ID",
    "SLURM_JOB_NAME",
    "SLURM_CPUS_PER_TASK",
)


@dataclass(frozen=True)
class ReprotrailSettings:
    """Project-level defaults loaded from ``[tool.reprotrail]``."""

    project_root: Path
    repos: tuple[str, ...] = (".",)
    product_root_markers: tuple[str, ...] = ()
    env_var_whitelist: tuple[str, ...] = DEFAULT_ENV_VAR_WHITELIST
    package_summary: tuple[str, ...] = ("reprotrail",)
    pixi_environment: str | None = None
    pixi_lockfile: str = "pixi.lock"
    extra: dict[str, Any] = field(default_factory=dict)


def _find_project_root(start: str | Path | None = None) -> Path:
    current = Path.cwd() if start is None else Path(start)
    current = current.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return current


def load_settings(project_root: str | Path | None = None) -> ReprotrailSettings:
    """Load ``[tool.reprotrail]`` settings from ``pyproject.toml`` if present."""

    root = _find_project_root(project_root)
    pyproject = root / "pyproject.toml"
    raw: dict[str, Any] = {}
    if pyproject.exists():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        raw = ((data.get("tool") or {}).get("reprotrail")) or {}

    if "license" in raw:
        raise ValueError(
            "[tool.reprotrail.license] is no longer supported. Select product licenses in "
            "reprotrail.products.toml or pass explicit product license metadata to product finalization."
        )
    known = {
        "repos",
        "product_root_markers",
        "env_var_whitelist",
        "package_summary",
        "pixi_environment",
        "pixi_lockfile",
    }
    return ReprotrailSettings(
        project_root=root,
        repos=tuple(str(item) for item in raw.get("repos", ["."])),
        product_root_markers=tuple(str(item) for item in raw.get("product_root_markers", [])),
        env_var_whitelist=tuple(str(item) for item in raw.get("env_var_whitelist", DEFAULT_ENV_VAR_WHITELIST)),
        package_summary=tuple(str(item) for item in raw.get("package_summary", [])) or ("reprotrail",),
        pixi_environment=(str(raw["pixi_environment"]) if raw.get("pixi_environment") else None),
        pixi_lockfile=str(raw.get("pixi_lockfile", "pixi.lock")),
        extra={str(key): value for key, value in raw.items() if key not in known},
    )
