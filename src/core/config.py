from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values, load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core workspace preferences
    workspace_path: str = Field(
        default="~/.migration-agent/workspaces",
        description="Path to isolated workspaces directory",
    )

    # Tiered models -- single source of truth, driven by config.yaml (ADR-0001)
    model_lite: str = Field(
        default="gemini/gemini-2.5-flash-lite",
        description="Fast model for low-risk transforms",
    )
    model_default: str = Field(
        default="gemini/gemini-2.5-flash",
        description="Default reasoning model for medium/high risk and fallback",
    )

    # Self-healing parameters
    max_healing_attempts: int = Field(
        default=3,
        description="Maximum self-healing attempts per file",
    )

    # Database URL for LangGraph checkpointer
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/migration_agent",
        description="PostgreSQL connection string for PostgresSaver checkpointer",
    )

    # API Keys and Secrets
    gemini_api_key: str | None = Field(
        default=None,
        description="Google Gemini API key",
    )
    langsmith_api_key: str | None = Field(
        default=None,
        description="LangSmith API key for tracing",
    )
    langsmith_project: str = Field(
        default="migration-agent",
        description="LangSmith project name",
    )
    langsmith_tracing: bool = Field(
        default=False,
        description="Whether LangSmith tracing is active",
    )
    tavily_api_key: str | None = Field(
        default=None,
        description="Tavily API key for web search fallback",
    )

    # Logging and Server settings
    log_level: str = Field(default="INFO", description="Log level")
    json_logs: bool = Field(default=True, description="Output structured JSON logs")
    api_host: str = Field(default="0.0.0.0", description="API bind host")
    api_port: int = Field(default=8000, description="API port")
    web_url: str = Field(
        default="http://localhost:3000", description="Web frontend URL"
    )

    # Directory layout defaults
    rules_dir: str = Field(default="src/rules", description="Rules directory")
    docs_corpus_dir: str = Field(
        default="src/docs_corpus", description="Docs corpus directory"
    )

    @property
    def resolved_workspace_path(self) -> Path:
        return Path(os.path.expanduser(self.workspace_path)).resolve()


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------
# All modules obtain settings via get_settings() or the zero-arg load_config()
# shortcut.  This guarantees a single Settings object is shared across the
# entire process, so changes applied at startup (config.yaml, CLI flags, API
# env-var overrides) are visible to every module without re-parsing the YAML.

_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide Settings singleton.

    Builds the singleton lazily from config.yaml on the very first call.
    Subsequent calls are O(1).
    """
    global _settings
    if _settings is None:
        _settings = _build_settings()
    return _settings


def configure_settings(settings: Settings) -> None:
    """Replace the process-wide singleton with a pre-built Settings instance.

    Call this once early in the process lifecycle (CLI entry-point, API startup)
    after resolving all overrides.  Every subsequent get_settings() or
    load_config() call in any module will return this instance.
    """
    global _settings
    _settings = settings


def _build_settings(
    config_path: Path | str | None = None,
    env_file: Path | str | None = None,
    **overrides: Any,
) -> Settings:
    """Parse config sources and return a fresh Settings instance (no caching)."""
    yaml_data: dict[str, Any] = {}

    target_path: Path | None = None
    if config_path:
        target_path = Path(config_path)
    elif Path("config.yaml").exists():
        target_path = Path("config.yaml")
    else:
        root_config = Path(__file__).resolve().parent.parent.parent / "config.yaml"
        if root_config.exists():
            target_path = root_config

    if target_path and target_path.exists():
        with open(target_path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f)
            if isinstance(content, dict):
                yaml_data = content

    if env_file and Path(env_file).exists():
        load_dotenv(dotenv_path=env_file, override=True)
        env_dict = dotenv_values(env_file)
        for k, v in env_dict.items():
            if v is not None:
                clean_k = k.lower().removeprefix("migration_agent_")
                yaml_data[clean_k] = v
    else:
        load_dotenv()

    # Environment variables prefixed with MIGRATION_AGENT_ override YAML values
    for key, val in os.environ.items():
        if key.startswith("MIGRATION_AGENT_"):
            clean_key = key[len("MIGRATION_AGENT_"):].lower()
            yaml_data[clean_key] = val

    # Direct env overrides for well-known secrets
    if "GEMINI_API_KEY" in os.environ:
        yaml_data["gemini_api_key"] = os.environ["GEMINI_API_KEY"]
    if "DATABASE_URL" in os.environ:
        yaml_data["database_url"] = os.environ["DATABASE_URL"]
    if "LANGSMITH_API_KEY" in os.environ:
        yaml_data["langsmith_api_key"] = os.environ["LANGSMITH_API_KEY"]
    if "TAVILY_API_KEY" in os.environ:
        yaml_data["tavily_api_key"] = os.environ["TAVILY_API_KEY"]

    # CLI flag overrides -- highest precedence
    for key, val in overrides.items():
        if val is not None:
            yaml_data[key] = val

    if env_file:
        return Settings(_env_file=env_file, **yaml_data)
    return Settings(**yaml_data)


def load_config(
    config_path: Path | str | None = None,
    env_file: Path | str | None = None,
    **overrides: Any,
) -> Settings:
    """Load settings and return the process-wide singleton.

    Zero-arg call ``load_config()``
        Returns the already-initialised singleton (builds from config.yaml on
        the first ever call). This is the hot path used by every internal module.

    Parameterised call ``load_config(config_path=..., model_lite=...)``
        Builds a fresh Settings from the given sources, REPLACES the singleton,
        and returns it.  The CLI and API entry-points use this form once at
        startup so every subsequent zero-arg call across all modules sees the
        same resolved values (including model_lite and model_default).
    """
    global _settings
    if config_path is None and env_file is None and not overrides:
        # Fast path -- return or lazily initialise the singleton.
        return get_settings()

    # Parameterised: rebuild and replace the process-wide singleton.
    _settings = _build_settings(
        config_path=config_path, env_file=env_file, **overrides
    )
    return _settings
