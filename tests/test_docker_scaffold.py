from pathlib import Path

import yaml


def test_docker_compose_valid_and_has_services():
    compose_path = Path("docker-compose.yml")
    assert compose_path.exists(), "docker-compose.yml must exist"

    with open(compose_path, "r", encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)

    services = compose_data.get("services", {})
    assert "postgres" in services, "Must have postgres service"
    assert "api" in services, "Must have api service"
    assert "web" in services, "Must have web service"

    # Check postgres image
    postgres = services["postgres"]
    assert "postgres:16" in postgres.get("image", "")

    # Check api has docker socket mount
    api = services["api"]
    volumes = api.get("volumes", [])
    has_docker_socket = any("/var/run/docker.sock" in str(v) for v in volumes)
    assert has_docker_socket, "API service must mount /var/run/docker.sock"

    # Check web has port 3000
    web = services["web"]
    ports = web.get("ports", [])
    has_port_3000 = any("3000" in str(p) for p in ports)
    assert has_port_3000, "Web service must expose port 3000"


def test_sandbox_dockerfile_supports_python_versions_and_tools():
    dockerfile_path = Path("docker/sandbox/Dockerfile")
    assert dockerfile_path.exists(), "docker/sandbox/Dockerfile must exist"

    content = dockerfile_path.read_text(encoding="utf-8")
    assert "ARG PYTHON_VERSION" in content, (
        "Sandbox Dockerfile must declare ARG PYTHON_VERSION"
    )
    assert "pytest" in content, "Sandbox Dockerfile must install pytest"
    assert "coverage" in content, "Sandbox Dockerfile must install coverage"


def test_env_example_has_all_placeholders():
    env_example = Path(".env.example")
    assert env_example.exists(), ".env.example must exist"

    content = env_example.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY" in content
    assert "LANGSMITH_API_KEY" in content
    assert "DATABASE_URL" in content
    assert "TAVILY_API_KEY" in content


def test_config_example_yaml_has_default_preferences():
    config_example = Path("config.example.yaml")
    assert config_example.exists(), "config.example.yaml must exist"

    with open(config_example, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert "workspace_path" in data
    assert "model_lite" in data
    assert "model_default" in data
    assert "max_healing_attempts" in data
