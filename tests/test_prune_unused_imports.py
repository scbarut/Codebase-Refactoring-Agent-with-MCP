from pathlib import Path
from src.core.file_subgraph import _prune_unused_imports


def test_prune_unused_imports_pydantic(tmp_path: Path):
    """Pydantic: ConfigDict should be removed if imported but not used."""
    code = (
        "from pydantic import BaseModel, Field, ConfigDict\n\n"
        "class Answer(BaseModel):\n"
        "    text: str = Field(...)\n"
    )
    test_file = tmp_path / "answer.py"
    test_file.write_text(code, encoding="utf-8")

    _prune_unused_imports(test_file)
    result = test_file.read_text(encoding="utf-8")

    assert "ConfigDict" not in result
    assert "BaseModel" in result
    assert "Field" in result


def test_prune_unused_imports_celery(tmp_path: Path):
    """Celery: shared_task should be removed if imported but not used."""
    code = (
        "from celery import Celery, shared_task\n\n"
        "app = Celery('myapp')\n"
    )
    test_file = tmp_path / "worker.py"
    test_file.write_text(code, encoding="utf-8")

    _prune_unused_imports(test_file)
    result = test_file.read_text(encoding="utf-8")

    assert "shared_task" not in result
    assert "Celery" in result


def test_prune_unused_imports_sqlalchemy(tmp_path: Path):
    """SQLAlchemy: func and Integer should be removed if imported but not used."""
    code = (
        "from sqlalchemy import select, func, Column, Integer\n\n"
        "def get_query(model):\n"
        "    return select(model)\n"
    )
    test_file = tmp_path / "db.py"
    test_file.write_text(code, encoding="utf-8")

    _prune_unused_imports(test_file)
    result = test_file.read_text(encoding="utf-8")

    assert "func" not in result
    assert "Integer" not in result
    assert "Column" not in result
    assert "select" in result


def test_prune_unused_imports_requests(tmp_path: Path):
    """Requests: json should be removed if imported but not used after migration."""
    code = (
        "import json\n"
        "import requests\n\n"
        "def fetch():\n"
        "    return requests.get('https://example.com', timeout=10)\n"
    )
    test_file = tmp_path / "api.py"
    test_file.write_text(code, encoding="utf-8")

    _prune_unused_imports(test_file)
    result = test_file.read_text(encoding="utf-8")

    assert "import json" not in result
    assert "import requests" in result


def test_prune_unused_imports_ignores_init_file(tmp_path: Path):
    """__init__.py should be untouched to preserve public re-exports."""
    code = "from .module import Service\n"
    init_file = tmp_path / "__init__.py"
    init_file.write_text(code, encoding="utf-8")

    _prune_unused_imports(init_file)
    result = init_file.read_text(encoding="utf-8")

    assert result == code
