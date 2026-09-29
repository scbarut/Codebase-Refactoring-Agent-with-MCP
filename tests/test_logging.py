import json
from io import StringIO

from src.core.logging import get_logger, setup_logging


def test_structured_json_logging():
    stream = StringIO()
    setup_logging(json_format=True, log_level="DEBUG", output_stream=stream)

    logger = get_logger("test_service")
    logger.info("service_started", service="api", port=8000)

    output = stream.getvalue().strip()
    assert output, "Output should not be empty"

    lines = [line for line in output.split("\n") if line.strip()]
    last_line = lines[-1]
    data = json.loads(last_line)

    assert data["event"] == "service_started"
    assert data["service"] == "api"
    assert data["port"] == 8000
    assert data["level"] == "info"
    assert "timestamp" in data
