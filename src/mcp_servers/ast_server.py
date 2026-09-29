from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider
from mcp.server.mcpserver import MCPServer

from src.core.logging import get_logger

logger = get_logger(__name__)

IGNORED_DIRS = {
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "build",
    "dist",
    ".mypy_cache",
    ".agents",
}


def _matches_target_library(module_name: str, target: str) -> bool:
    """Check if imported module matches target library or is a submodule."""
    return module_name == target or module_name.startswith(f"{target}.")


def _extract_parameters(
    params: cst.Parameters, mod: cst.Module
) -> list[dict[str, Any]]:
    """Extract structured details for all parameter types in a FunctionDef."""
    results: list[dict[str, Any]] = []

    def _process_param(param: cst.Param, prefix: str = "") -> dict[str, Any]:
        p_name = f"{prefix}{param.name.value}"
        annot = (
            mod.code_for_node(param.annotation.annotation).strip()
            if param.annotation
            else None
        )
        default = mod.code_for_node(param.default).strip() if param.default else None

        formatted = p_name
        if annot:
            formatted += f": {annot}"
        if default:
            formatted += f" = {default}"

        return {
            "name": param.name.value,
            "prefix": prefix,
            "type_annotation": annot,
            "default_value": default,
            "formatted": formatted,
        }

    for p in params.posonly_params:
        results.append(_process_param(p))
    for p in params.params:
        results.append(_process_param(p))
    if isinstance(params.star_arg, cst.Param):
        results.append(_process_param(params.star_arg, prefix="*"))
    for p in params.kwonly_params:
        results.append(_process_param(p))
    if params.star_kwarg:
        results.append(_process_param(params.star_kwarg, prefix="**"))

    return results


class _SignatureCollector(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, module: cst.Module):
        self.module = module
        self.classes: list[dict[str, Any]] = []
        self.functions: list[dict[str, Any]] = []
        self._current_class: dict[str, Any] | None = None
        self._function_depth: int = 0

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        pos = self.get_metadata(PositionProvider, node)
        cls_info: dict[str, Any] = {
            "name": node.name.value,
            "type": "class",
            "decorators": [
                self.module.code_for_node(d).strip() for d in node.decorators
            ],
            "bases": [self.module.code_for_node(b.value).strip() for b in node.bases],
            "start_line": pos.start.line,
            "end_line": pos.end.line,
            "methods": [],
        }
        self.classes.append(cls_info)
        self._current_class = cls_info

    def leave_ClassDef(self, node: cst.ClassDef) -> None:
        self._current_class = None

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        if self._function_depth > 0:
            self._function_depth += 1
            return

        self._function_depth += 1
        pos = self.get_metadata(PositionProvider, node)
        return_annot = (
            self.module.code_for_node(node.returns.annotation).strip()
            if node.returns
            else None
        )
        fn_info: dict[str, Any] = {
            "name": node.name.value,
            "type": "function",
            "decorators": [
                self.module.code_for_node(d).strip() for d in node.decorators
            ],
            "arguments": _extract_parameters(node.params, self.module),
            "return_annotation": return_annot,
            "is_async": node.asynchronous is not None,
            "start_line": pos.start.line,
            "end_line": pos.end.line,
        }

        if self._current_class is not None:
            self._current_class["methods"].append(fn_info)
        else:
            self.functions.append(fn_info)

    def leave_FunctionDef(self, node: cst.FunctionDef) -> None:
        self._function_depth -= 1


class _NodeContextFinder(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, target_symbol: str, module: cst.Module):
        self.target_symbol = target_symbol
        self.module = module
        self.found_node: cst.ClassDef | cst.FunctionDef | None = None
        self.node_type: str | None = None
        self.start_line: int | None = None
        self.end_line: int | None = None
        self._current_class_name: str | None = None
        self._fallback_method_node: cst.FunctionDef | None = None
        self._fallback_start_line: int | None = None
        self._fallback_end_line: int | None = None

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        cls_name = node.name.value
        self._current_class_name = cls_name
        if self.target_symbol == cls_name and self.found_node is None:
            self.found_node = node
            self.node_type = "class"
            pos = self.get_metadata(PositionProvider, node)
            self.start_line = pos.start.line
            self.end_line = pos.end.line

    def leave_ClassDef(self, node: cst.ClassDef) -> None:
        self._current_class_name = None

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        if self.found_node is not None:
            return

        fn_name = node.name.value
        qualname = (
            f"{self._current_class_name}.{fn_name}"
            if self._current_class_name
            else fn_name
        )
        pos = self.get_metadata(PositionProvider, node)

        if "." in self.target_symbol:
            if qualname == self.target_symbol:
                self.found_node = node
                self.node_type = "function"
                self.start_line = pos.start.line
                self.end_line = pos.end.line
        else:
            if self._current_class_name is None and fn_name == self.target_symbol:
                self.found_node = node
                self.node_type = "function"
                self.start_line = pos.start.line
                self.end_line = pos.end.line
            elif self._current_class_name is not None and fn_name == self.target_symbol:
                if self._fallback_method_node is None:
                    self._fallback_method_node = node
                    self._fallback_start_line = pos.start.line
                    self._fallback_end_line = pos.end.line


def create_ast_server() -> MCPServer:
    """Create and configure the mcp-server-ast MCP server."""
    server = MCPServer("mcp-server-ast")

    @server.tool()
    def scan_imports(
        directory_path: str,
        target_library: str,
    ) -> dict[str, Any]:
        """Scan a directory for Python files importing a target library.

        Uses Python's built-in ast module for fast pre-filtering, ignoring
        virtual environments, cache dirs, and git directories.

        Args:
            directory_path: Absolute or relative directory path to scan.
            target_library: Name of the target library or package (e.g. 'pydantic').

        Returns:
            Dict containing directory_path, target_library, matched files list,
            relative_files list, and count.
        """
        dir_path = Path(directory_path).resolve()
        if not dir_path.exists() or not dir_path.is_dir():
            raise FileNotFoundError(f"Directory not found: {directory_path}")

        matched_files: list[Path] = []

        for py_path in dir_path.rglob("*.py"):
            rel_parts = py_path.relative_to(dir_path).parts
            if any(part in IGNORED_DIRS for part in rel_parts):
                continue

            try:
                content = py_path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(content, filename=str(py_path))
            except (SyntaxError, UnicodeDecodeError, OSError) as exc:
                logger.debug(
                    "Skipping unparseable file during scan",
                    path=str(py_path),
                    error=str(exc),
                )
                continue

            imported = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if _matches_target_library(alias.name, target_library):
                            imported = True
                            break
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and _matches_target_library(node.module, target_library)
                ):
                    imported = True
                    break
                if imported:
                    break

            if imported:
                matched_files.append(py_path)

        matched_files.sort()

        return {
            "directory_path": str(dir_path).replace("\\", "/"),
            "target_library": target_library,
            "files": [str(p.resolve()).replace("\\", "/") for p in matched_files],
            "relative_files": [
                str(p.relative_to(dir_path)).replace("\\", "/") for p in matched_files
            ],
            "count": len(matched_files),
        }

    @server.tool()
    def extract_signatures(
        file_path: str,
    ) -> dict[str, Any]:
        """Extract structured function and class signatures from a Python file via libcst.

        Args:
            file_path: Path to the Python source file.

        Returns:
            Dict containing file_path, classes (with decorators, bases, methods),
            and standalone functions (with decorators, parameters, return annotation).
        """
        path = Path(file_path).resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Source file not found: {file_path}")

        source_code = path.read_text(encoding="utf-8")
        tree = cst.parse_module(source_code)
        wrapper = MetadataWrapper(tree)
        collector = _SignatureCollector(tree)
        wrapper.visit(collector)

        return {
            "file_path": str(path).replace("\\", "/"),
            "classes": collector.classes,
            "functions": collector.functions,
            "signatures": collector.classes + collector.functions,
        }

    @server.tool()
    def apply_transform(
        file_path: str,
        transformer_path: str,
        write: bool = False,
    ) -> dict[str, Any]:
        """Apply a CSTTransformer (referenced by dotted Python path) to a Python file.

        Preserves the original file's formatting, whitespace, and comments.

        Args:
            file_path: Path to the Python source file.
            transformer_path: Dotted Python path to the CSTTransformer class
                             (e.g., 'src.rules.pydantic.FieldRenameTransformer').
            write: If True, writes the transformed source code back to disk.
                   Defaults to False (dry run).

        Returns:
            Dict containing file_path, transformer_path, modified bool,
            written_to_disk bool, original_code, and transformed_code.
        """
        path = Path(file_path).resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Source file not found: {file_path}")

        if "." not in transformer_path:
            raise ValueError(
                f"Invalid transformer path '{transformer_path}'. Expected dotted path like 'pkg.module.ClassName'."
            )

        module_name, class_name = transformer_path.rsplit(".", 1)
        try:
            mod = importlib.import_module(module_name)
        except Exception as e:
            raise ImportError(f"Could not import module '{module_name}': {e}") from e

        if not hasattr(mod, class_name):
            raise AttributeError(
                f"Module '{module_name}' has no attribute '{class_name}'"
            )

        transformer_cls = getattr(mod, class_name)
        if not isinstance(transformer_cls, type) or not issubclass(
            transformer_cls, cst.CSTTransformer
        ):
            raise TypeError(
                f"'{transformer_path}' is not a subclass of libcst.CSTTransformer"
            )

        transformer = transformer_cls()
        source_code = path.read_text(encoding="utf-8")
        tree = cst.parse_module(source_code)

        if getattr(transformer, "METADATA_DEPENDENCIES", None):
            wrapper = MetadataWrapper(tree)
            new_tree = wrapper.visit(transformer)
        else:
            new_tree = tree.visit(transformer)

        modified = new_tree.code != source_code
        written_to_disk = False

        if write and modified:
            path.write_text(new_tree.code, encoding="utf-8")
            written_to_disk = True

        return {
            "file_path": str(path).replace("\\", "/"),
            "transformer_path": transformer_path,
            "modified": modified,
            "written_to_disk": written_to_disk,
            "original_code": source_code,
            "transformed_code": new_tree.code,
        }

    @server.tool()
    def get_node_context(
        file_path: str,
        symbol_name: str,
    ) -> dict[str, Any]:
        """Extract the exact source code of a specific AST node (function or class).

        Facilitates isolated LLM processing with precise boundary context.

        Args:
            file_path: Path to the Python source file.
            symbol_name: Target symbol name (e.g., 'UserModel', 'process_item',
                         or qualified 'UserModel.validate_name').

        Returns:
            Dict containing file_path, symbol_name, node_type, source_code,
            start_line, and end_line.
        """
        path = Path(file_path).resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Source file not found: {file_path}")

        source_code = path.read_text(encoding="utf-8")
        tree = cst.parse_module(source_code)
        wrapper = MetadataWrapper(tree)
        finder = _NodeContextFinder(symbol_name, tree)
        wrapper.visit(finder)

        if finder.found_node is None and finder._fallback_method_node is not None:
            finder.found_node = finder._fallback_method_node
            finder.node_type = "function"
            finder.start_line = finder._fallback_start_line
            finder.end_line = finder._fallback_end_line

        if finder.found_node is None or finder.node_type is None:
            raise ValueError(f"Symbol '{symbol_name}' not found in {file_path}")

        node_code = tree.code_for_node(finder.found_node)

        return {
            "file_path": str(path).replace("\\", "/"),
            "symbol_name": symbol_name,
            "node_type": finder.node_type,
            "source_code": node_code,
            "start_line": finder.start_line,
            "end_line": finder.end_line,
        }

    return server


def main() -> None:
    """Run the mcp-server-ast server with stdio transport."""
    server = create_ast_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
