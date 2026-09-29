from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Tiered models
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

    # API Keys & Secrets
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

    # Logging & Server settings
    log_level: str = Field(default="INFO", description="Log level (DEBUG, INFO, etc.)")
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


def load_config(config_path: Path | str | None = None) -> Settings:
    """Load settings from optional YAML file and environment variables."""
    yaml_data: dict[str, Any] = {}

    target_path: Path | None = None
    if config_path:
        target_path = Path(config_path)
    elif Path("config.yaml").exists():
        target_path = Path("config.yaml")

    if target_path and target_path.exists():
        with open(target_path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f)
            if isinstance(content, dict):
                yaml_data = content

    # Allow environment variables prefixed with MIGRATION_AGENT_ to override YAML values
    for key, val in os.environ.items():
        if key.startswith("MIGRATION_AGENT_"):
            clean_key = key[len("MIGRATION_AGENT_") :].lower()
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

    return Settings(**yaml_data)
