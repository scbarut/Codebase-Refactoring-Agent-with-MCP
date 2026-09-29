import json
from pathlib import Path

import libcst as cst
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_servers.ast_server import create_ast_server


class DummyRenameFunctionTransformer(cst.CSTTransformer):
    """Test transformer that renames function 'old_function' to 'new_function'."""

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        if original_node.name.value == "old_function":
            return updated_node.with_changes(name=cst.Name("new_function"))
        return updated_node


class DummyRenameFieldTransformer(cst.CSTTransformer):
    """Test transformer that renames attribute 'regex' to 'pattern' in function calls or assignments."""

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:
        if original_node.value == "regex":
            return updated_node.with_changes(value="pattern")
        return updated_node


@pytest.mark.asyncio
async def test_tool_discovery():
    server = create_ast_server()
    tools = await server.list_tools()
    tool_names = {t.name for t in tools}
    expected_tools = {
        "scan_imports",
        "extract_signatures",
        "apply_transform",
        "get_node_context",
    }
    assert expected_tools.issubset(tool_names)
    for tool in tools:
        assert tool.description


@pytest.mark.asyncio
async def test_scan_imports_finds_targeted_files(tmp_path: Path):
    server = create_ast_server()

    # 1. Direct import: import pydantic
    f1 = tmp_path / "f1.py"
    f1.write_text("import pydantic\n\nclass M: pass\n", encoding="utf-8")

    # 2. Submodule import: import pydantic.v1 as pyd
    f2 = tmp_path / "f2.py"
    f2.write_text("import pydantic.v1 as pyd\n", encoding="utf-8")

    # 3. From import: from pydantic import BaseModel, Field
    sub_dir = tmp_path / "subpkg"
    sub_dir.mkdir()
    f3 = sub_dir / "f3.py"
    f3.write_text("from pydantic import BaseModel, Field\n", encoding="utf-8")

    # 4. From submodule import: from pydantic.fields import FieldInfo
    f4 = sub_dir / "f4.py"
    f4.write_text("from pydantic.fields import FieldInfo\n", encoding="utf-8")

    # 5. Multiple imports on one line: import sys, pydantic, os
    f5 = tmp_path / "f5.py"
    f5.write_text("import sys, pydantic, os\n", encoding="utf-8")

    # 6. Unrelated file: should NOT match
    f_unrelated = tmp_path / "unrelated.py"
    f_unrelated.write_text("import os\nfrom pathlib import Path\n", encoding="utf-8")

    # 7. File with syntax error: should NOT crash the scanner
    f_broken = tmp_path / "broken.py"
    f_broken.write_text("def invalid_syntax(\n", encoding="utf-8")

    # 8. Target inside ignored directory: should be ignored
    venv_dir = tmp_path / ".venv" / "lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "ignored.py").write_text("import pydantic\n", encoding="utf-8")

    result = await server.call_tool(
        "scan_imports",
        {
            "directory_path": str(tmp_path),
            "target_library": "pydantic",
        },
    )

    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["target_library"] == "pydantic"
    assert data["count"] == 5

    matched_files = {Path(p).name for p in data["files"]}
    assert matched_files == {"f1.py", "f2.py", "f3.py", "f4.py", "f5.py"}
    assert "unrelated.py" not in matched_files
    assert "broken.py" not in matched_files
    assert "ignored.py" not in matched_files


@pytest.mark.asyncio
async def test_scan_imports_nonexistent_directory(tmp_path: Path):
    server = create_ast_server()
    with pytest.raises(ToolError):
        await server.call_tool(
            "scan_imports",
            {
                "directory_path": str(tmp_path / "nonexistent_dir"),
                "target_library": "pydantic",
            },
        )


@pytest.mark.asyncio
async def test_extract_signatures(tmp_path: Path):
    server = create_ast_server()

    code = '''"""Sample module docstring."""
from typing import Optional, List

@decorator_a
@decorator_b(arg="val")
class UserModel(BaseModel, Sequence[str]):
    """User representation model."""
    id: int
    name: str

    @classmethod
    def create_guest(cls, name: str = "guest") -> "UserModel":
        return cls(id=0, name=name)

    async def update_email(self, new_email: str) -> bool:
        return True


@cache
def standalone_func(x: int, y: Optional[List[str]] = None, *args, **kwargs) -> int:
    return x * 2
'''
    py_file = tmp_path / "module.py"
    py_file.write_text(code, encoding="utf-8")

    result = await server.call_tool(
        "extract_signatures",
        {
            "file_path": str(py_file),
        },
    )

    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert "classes" in data
    assert "functions" in data
    assert len(data["classes"]) == 1
    assert len(data["functions"]) == 1

    # Check class signature
    cls_sig = data["classes"][0]
    assert cls_sig["name"] == "UserModel"
    assert cls_sig["type"] == "class"
    assert "BaseModel" in cls_sig["bases"]
    assert any("Sequence" in b for b in cls_sig["bases"])
    assert any("decorator_a" in d for d in cls_sig["decorators"])
    assert any("decorator_b" in d for d in cls_sig["decorators"])
    assert len(cls_sig["methods"]) == 2

    # Check class methods
    m1 = cls_sig["methods"][0]
    assert m1["name"] == "create_guest"
    assert any("classmethod" in d for d in m1["decorators"])
    assert "name" in [
        a.get("name") if isinstance(a, dict) else a for a in m1["arguments"]
    ]

    m2 = cls_sig["methods"][1]
    assert m2["name"] == "update_email"
    assert m2["is_async"] is True

    # Check standalone function signature
    fn_sig = data["functions"][0]
    assert fn_sig["name"] == "standalone_func"
    assert fn_sig["type"] == "function"
    assert any("cache" in d for d in fn_sig["decorators"])
    arg_names = [
        a["name"] if isinstance(a, dict) else a.split(":")[0].strip()
        for a in fn_sig["arguments"]
    ]
    assert "x" in arg_names
    assert "y" in arg_names


@pytest.mark.asyncio
async def test_extract_signatures_nonexistent_file(tmp_path: Path):
    server = create_ast_server()
    with pytest.raises(ToolError):
        await server.call_tool(
            "extract_signatures",
            {
                "file_path": str(tmp_path / "does_not_exist.py"),
            },
        )


@pytest.mark.asyncio
async def test_apply_transform_preserves_formatting_and_comments(tmp_path: Path):
    server = create_ast_server()

    original_code = '''# Header comment: File configuration
# Leading copyright note

import math  # inline comment on import


# Comment before old_function
def old_function(x: int = 10, y: str = "default") -> int:
    """Docstring with details."""
    # Internal explanation comment
    temp = x + 5  # Calculation
    return temp


def untouched_function():
    # Stays completely untouched
    pass
'''
    test_file = tmp_path / "transform_target.py"
    test_file.write_text(original_code, encoding="utf-8")

    transformer_path = "tests.test_mcp_ast.DummyRenameFunctionTransformer"

    # Test without write (dry run)
    result = await server.call_tool(
        "apply_transform",
        {
            "file_path": str(test_file),
            "transformer_path": transformer_path,
            "write": False,
        },
    )

    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["modified"] is True
    assert data["written_to_disk"] is False
    transformed_code = data["transformed_code"]

    # Verify formatting, comments, whitespace preserved
    assert "# Header comment: File configuration" in transformed_code
    assert "# Leading copyright note" in transformed_code
    assert "import math  # inline comment on import" in transformed_code
    assert "# Comment before old_function" in transformed_code
    assert "def new_function(x: int = 10, y: str = " in transformed_code
    assert '"""Docstring with details."""' in transformed_code
    assert "# Internal explanation comment" in transformed_code
    assert "temp = x + 5  # Calculation" in transformed_code
    assert "def untouched_function():" in transformed_code
    assert "# Stays completely untouched" in transformed_code

    # File on disk should still be original because write=False
    assert test_file.read_text(encoding="utf-8") == original_code

    # Now test with write=True
    write_res = await server.call_tool(
        "apply_transform",
        {
            "file_path": str(test_file),
            "transformer_path": transformer_path,
            "write": True,
        },
    )
    assert not write_res.is_error
    write_data = json.loads(write_res.content[0].text)
    assert write_data["written_to_disk"] is True
    assert test_file.read_text(encoding="utf-8") == transformed_code


@pytest.mark.asyncio
async def test_apply_transform_invalid_transformer(tmp_path: Path):
    server = create_ast_server()
    py_file = tmp_path / "dummy.py"
    py_file.write_text("x = 1\n", encoding="utf-8")

    # Not a dotted path
    with pytest.raises(ToolError):
        await server.call_tool(
            "apply_transform",
            {
                "file_path": str(py_file),
                "transformer_path": "NonDottedName",
            },
        )

    # Class does not exist
    with pytest.raises(ToolError):
        await server.call_tool(
            "apply_transform",
            {
                "file_path": str(py_file),
                "transformer_path": "tests.test_mcp_ast.NonExistentTransformer",
            },
        )

    # Class exists but is not a CSTTransformer
    with pytest.raises(ToolError):
        await server.call_tool(
            "apply_transform",
            {
                "file_path": str(py_file),
                "transformer_path": "json.JSONDecoder",
            },
        )


@pytest.mark.asyncio
async def test_get_node_context(tmp_path: Path):
    server = create_ast_server()

    code = '''import os

class UserProfile:
    """User profile class."""
    def __init__(self, username: str):
        self.username = username

    def get_display_name(self) -> str:
        # Return uppercase name
        return self.username.upper()


def calculate_score(points: int, multiplier: float = 1.0) -> float:
    # Calculate weighted score
    return points * multiplier
'''
    py_file = tmp_path / "context_target.py"
    py_file.write_text(code, encoding="utf-8")

    # 1. Extract class node
    class_res = await server.call_tool(
        "get_node_context",
        {
            "file_path": str(py_file),
            "symbol_name": "UserProfile",
        },
    )
    assert not class_res.is_error
    class_data = json.loads(class_res.content[0].text)
    assert class_data["symbol_name"] == "UserProfile"
    assert class_data["node_type"] == "class"
    assert "class UserProfile:" in class_data["source_code"]
    assert "def get_display_name(self) -> str:" in class_data["source_code"]
    assert class_data["start_line"] == 3

    # 2. Extract function node
    func_res = await server.call_tool(
        "get_node_context",
        {
            "file_path": str(py_file),
            "symbol_name": "calculate_score",
        },
    )
    assert not func_res.is_error
    func_data = json.loads(func_res.content[0].text)
    assert func_data["symbol_name"] == "calculate_score"
    assert func_data["node_type"] == "function"
    assert (
        "def calculate_score(points: int, multiplier: float = 1.0) -> float:"
        in func_data["source_code"]
    )
    assert "# Calculate weighted score" in func_data["source_code"]

    # 3. Extract method via qualified name
    method_res = await server.call_tool(
        "get_node_context",
        {
            "file_path": str(py_file),
            "symbol_name": "UserProfile.get_display_name",
        },
    )
    assert not method_res.is_error
    method_data = json.loads(method_res.content[0].text)
    assert method_data["symbol_name"] == "UserProfile.get_display_name"
    assert method_data["node_type"] == "function"
    assert "def get_display_name(self) -> str:" in method_data["source_code"]

    # 4. Extract method without class qualifier fallback
    fallback_res = await server.call_tool(
        "get_node_context",
        {
            "file_path": str(py_file),
            "symbol_name": "get_display_name",
        },
    )
    assert not fallback_res.is_error
    fallback_data = json.loads(fallback_res.content[0].text)
    assert fallback_data["node_type"] == "function"
    assert "def get_display_name(self) -> str:" in fallback_data["source_code"]

    # 5. Symbol not found
    with pytest.raises(ToolError):
        await server.call_tool(
            "get_node_context",
            {
                "file_path": str(py_file),
                "symbol_name": "non_existent_symbol",
            },
        )


@pytest.mark.asyncio
async def test_get_node_context_nonexistent_file(tmp_path: Path):
    server = create_ast_server()
    with pytest.raises(ToolError):
        await server.call_tool(
            "get_node_context",
            {
                "file_path": str(tmp_path / "ghost.py"),
                "symbol_name": "foo",
            },
        )


@pytest.mark.asyncio
async def test_extract_signatures_nested_closure(tmp_path: Path):
    server = create_ast_server()

    code = """def outer_function(a: int) -> int:
    def inner_helper(b: int) -> int:
        return b * 2
    return inner_helper(a)
"""
    py_file = tmp_path / "closure_test.py"
    py_file.write_text(code, encoding="utf-8")

    result = await server.call_tool(
        "extract_signatures",
        {
            "file_path": str(py_file),
        },
    )
    assert not result.is_error
    data = json.loads(result.content[0].text)
    # Only outer_function should be in top-level functions; inner_helper should not pollute it
    assert len(data["functions"]) == 1
    assert data["functions"][0]["name"] == "outer_function"


@pytest.mark.asyncio
async def test_apply_transform_no_changes(tmp_path: Path):
    server = create_ast_server()

    code = "def unchanged():\n    pass\n"
    py_file = tmp_path / "no_change.py"
    py_file.write_text(code, encoding="utf-8")

    result = await server.call_tool(
        "apply_transform",
        {
            "file_path": str(py_file),
            "transformer_path": "tests.test_mcp_ast.DummyRenameFunctionTransformer",
            "write": True,
        },
    )
    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["modified"] is False
    assert data["written_to_disk"] is False
    assert data["transformed_code"] == code
