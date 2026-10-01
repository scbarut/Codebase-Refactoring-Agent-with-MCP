from pathlib import Path

import pytest
import yaml

from src.core.config import Settings, load_config


def test_default_config():
    settings = Settings()
    assert settings.workspace_path == "~/.migration-agent/workspaces"
    assert "gemini" in settings.model_lite
    assert "gemini" in settings.model_default
    assert settings.max_healing_attempts == 3
    assert settings.log_level == "INFO"
    assert settings.json_logs is True


def test_load_config_from_yaml(tmp_path: Path):
    custom_yaml = tmp_path / "config.yaml"
    data = {
        "workspace_path": "/tmp/custom-workspaces",
        "model_lite": "custom-lite",
        "model_default": "custom-default",
        "max_healing_attempts": 5,
        "log_level": "DEBUG",
        "json_logs": False,
    }
    custom_yaml.write_text(yaml.dump(data), encoding="utf-8")

    settings = load_config(config_path=custom_yaml)
    assert settings.workspace_path == "/tmp/custom-workspaces"
    assert settings.model_lite == "custom-lite"
    assert settings.model_default == "custom-default"
    assert settings.max_healing_attempts == 5
    assert settings.log_level == "DEBUG"
    assert settings.json_logs is False


def test_load_config_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MIGRATION_AGENT_MAX_HEALING_ATTEMPTS", "7")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db:5432/testdb")

    settings = load_config()
    assert settings.max_healing_attempts == 7
    assert settings.gemini_api_key == "test-key-123"
    assert settings.database_url == "postgresql://user:pass@db:5432/testdb"


def test_load_config_cli_overrides(tmp_path: Path):
    custom_yaml = tmp_path / "config.yaml"
    custom_yaml.write_text(
        yaml.dump({"workspace_path": "/from/yaml", "model_lite": "yaml-lite"}),
        encoding="utf-8",
    )

    # Overrides should take precedence over yaml and defaults
    settings = load_config(
        config_path=custom_yaml,
        workspace_path="/from/cli",
        model_lite="cli-lite",
        model_default="cli-default",
    )
    assert settings.workspace_path == "/from/cli"
    assert settings.model_lite == "cli-lite"
    assert settings.model_default == "cli-default"


def test_load_config_custom_env_file(tmp_path: Path):
    custom_env = tmp_path / ".env.custom"
    custom_env.write_text(
        "WORKSPACE_PATH=/from/custom/env\nLOG_LEVEL=DEBUG\n", encoding="utf-8"
    )

    settings = load_config(env_file=custom_env)
    assert settings.workspace_path == "/from/custom/env"
    assert settings.log_level == "DEBUG"
