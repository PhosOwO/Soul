from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from soul.services.shared.constants import (
    REME_LLM_API_KEY_ENV,
    REME_LLM_BASE_URL_ENV,
    REME_LLM_MODEL_NAME_ENV,
    SOUL_HOME_ENV,
)


@dataclass(frozen=True, slots=True)
class ReMeRuntimeConfig:
    env: dict[str, str]
    values: dict[str, str]
    sources: dict[str, str]
    env_files: list[Path]

    @property
    def missing(self) -> list[str]:
        return [name for name in reme_llm_env_names() if not self.values.get(name)]


def reme_llm_env_names() -> list[str]:
    return [REME_LLM_API_KEY_ENV, REME_LLM_BASE_URL_ENV, REME_LLM_MODEL_NAME_ENV]


def build_reme_subprocess_env(project_dir: Path) -> dict[str, str]:
    return resolve_reme_runtime_config(project_dir).env


def resolve_reme_runtime_config(project_dir: Path, *, home_dir: Path | None = None) -> ReMeRuntimeConfig:
    env = os.environ.copy()
    sources = {name: "environment" for name in reme_llm_env_names() if env.get(name)}
    env_files = find_reme_env_files(project_dir, home_dir=home_dir)
    for env_file in env_files:
        for key, value in parse_simple_env_file(env_file).items():
            env[key] = value
            if key in reme_llm_env_names() and value:
                sources[key] = f".env ({env_file})"

    values = {name: env.get(name, "") for name in reme_llm_env_names()}
    if any(not values.get(name) for name in reme_llm_env_names()):
        for config_path in host_config_candidates(project_dir):
            inferred = infer_reme_env_from_host_config(config_path, env)
            for key, value in inferred.items():
                if not values.get(key) and value:
                    env[key] = value
                    values[key] = value
                    sources[key] = f"host config ({config_path})"
            if all(values.get(name) for name in reme_llm_env_names()):
                break

    return ReMeRuntimeConfig(env=env, values=values, sources=sources, env_files=env_files)


def find_reme_env_files(project_dir: Path, *, home_dir: Path | None = None) -> list[Path]:
    files: list[Path] = []
    global_env = soul_home(home_dir) / ".env"
    if global_env.exists():
        files.append(global_env)
    for directory in [project_dir, *project_dir.parents[:5]]:
        env_path = directory / ".env"
        if env_path.exists():
            files.append(env_path)
            break
    return files


def soul_home(home_dir: Path | None = None) -> Path:
    configured = os.environ.get(SOUL_HOME_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return (home_dir or Path.home()) / ".soul"


def parse_simple_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip("'\"")
    return values


def host_config_candidates(project_dir: Path) -> list[Path]:
    candidates = [
        project_dir / ".trae" / "traecli.toml",
        project_dir / ".codex" / "config.toml",
    ]
    trae_home = os.environ.get("TRAE_HOME")
    if trae_home:
        candidates.append(Path(trae_home).expanduser() / "traecli.toml")
    candidates.append(Path.home() / ".trae" / "traecli.toml")
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        candidates.append(Path(codex_home).expanduser() / "config.toml")
    candidates.append(Path.home() / ".codex" / "config.toml")
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.expanduser().resolve()
        if resolved not in seen:
            deduped.append(resolved)
            seen.add(resolved)
    return deduped


def infer_reme_env_from_host_config(path: Path, env: dict[str, str]) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}

    provider = find_openai_provider(data)
    if provider is None:
        return {}

    resolved: dict[str, str] = {}
    api_key = resolve_config_value(provider.get("api_key"), env)
    base_url = resolve_config_value(provider.get("base_url"), env)
    model = resolve_config_value(provider.get("model"), env) or resolve_config_value(data.get("model"), env)
    if api_key:
        resolved[REME_LLM_API_KEY_ENV] = api_key
    if base_url:
        resolved[REME_LLM_BASE_URL_ENV] = base_url
    if model:
        resolved[REME_LLM_MODEL_NAME_ENV] = model
    return resolved


def find_openai_provider(data: object) -> dict[str, object] | None:
    if not isinstance(data, dict):
        return None
    models = data.get("models")
    if isinstance(models, dict):
        provider = models.get("open_ai") or models.get("openai")
        return provider if isinstance(provider, dict) else None
    if isinstance(models, list):
        for item in models:
            if isinstance(item, dict):
                provider = item.get("open_ai") or item.get("openai")
                if isinstance(provider, dict):
                    return provider
    providers = data.get("model_providers")
    if isinstance(providers, dict):
        for provider in providers.values():
            if isinstance(provider, dict) and provider.get("base_url"):
                return provider
    return None


def resolve_config_value(value: object, env: dict[str, str]) -> str:
    if not isinstance(value, str):
        return ""
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value.strip())
    if match:
        return env.get(match.group(1), "")
    return value.strip()
