"""Config loading: .corbit.toml > env vars > CLI flags."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from corbit.models import AgentBackend, CorbitConfig, IterationMode, MergeMethod, MergeStrategy

_CONFIG_FILENAME = ".corbit.toml"

_ENV_PREFIX = "CORBIT_"

_ENV_MAP: dict[str, str] = {
    "CORBIT_BACKEND": "coder_backend",
    "CORBIT_REVIEWER_BACKEND": "reviewer_backend",
    "CORBIT_MAX_ROUNDS": "max_review_rounds",
    "CORBIT_ITERATION_MODE": "iteration_mode",
    "CORBIT_PARALLEL": "parallel_workers",
    "CORBIT_MAIN_BRANCH": "main_branch",
    "CORBIT_AGENT_TIMEOUT": "agent_timeout",
    "CORBIT_CODER_MODEL": "coder_model",
    "CORBIT_REVIEWER_MODEL": "reviewer_model",
    "LINEAR_API_KEY": "linear_api_key",
}

_INT_FIELDS = {"max_review_rounds", "parallel_workers", "agent_timeout"}


def _find_config_file() -> Path | None:
    path = Path.cwd()
    for parent in [path, *path.parents]:
        candidate = parent / _CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _load_toml(path: Path) -> dict[str, object]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return data.get("corbit", {})  # type: ignore[return-value]


def _load_env() -> dict[str, object]:
    overrides: dict[str, object] = {}
    for env_key, field_name in _ENV_MAP.items():
        value = os.environ.get(env_key)
        if value is None:
            continue
        if field_name in _INT_FIELDS:
            overrides[field_name] = int(value)
        else:
            overrides[field_name] = value
    return overrides


def load_config(
    backend: str | None = None,
    reviewer_backend: str | None = None,
    max_rounds: int | None = None,
    iteration_mode: str | None = None,
    workers: int | None = None,
    parallel: bool = False,
    main_branch: str | None = None,
    debug: bool = False,
    merge_method: str | None = None,
    clean: bool = False,
    merge_strategy: str | None = None,
) -> CorbitConfig:
    """Load config with 3-layer precedence: toml < env < CLI flags."""
    merged: dict[str, object] = {}

    config_path = _find_config_file()
    if config_path is not None:
        merged.update(_load_toml(config_path))

    merged.update(_load_env())

    if backend is not None:
        merged["coder_backend"] = AgentBackend(backend)
    if reviewer_backend is not None:
        merged["reviewer_backend"] = AgentBackend(reviewer_backend)
    if max_rounds is not None:
        merged["max_review_rounds"] = max_rounds
    if iteration_mode is not None:
        merged["iteration_mode"] = IterationMode(iteration_mode)
    if workers is not None:
        merged["parallel_workers"] = workers
    if parallel:
        merged["sequential"] = False
    if main_branch is not None:
        merged["main_branch"] = main_branch
    if debug:
        merged["debug"] = True
    if merge_method is not None:
        merged["merge_method"] = MergeMethod(merge_method)
    if clean:
        merged["clean"] = True
    if merge_strategy is not None:
        merged["merge_strategy"] = MergeStrategy(merge_strategy)

    return CorbitConfig(**merged)  # type: ignore[arg-type]
