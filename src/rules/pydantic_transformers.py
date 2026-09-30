"""CSTTransformer classes for Pydantic v1 → v2 migration.

Each transformer handles one category of mechanical rewrite.  They are
designed to be applied independently via ``mcp-server-ast``'s
``apply_transform`` tool, so every class is importable by dotted path.

All transformers preserve formatting, whitespace, and comments by
operating on the libcst Concrete Syntax Tree rather than the abstract
AST.
"""

from __future__ import annotations

from collections.abc import Sequence

import libcst as cst
import libcst.matchers as m

# ── Helpers ────────────────────────────────────────────────────────────


def _arg_value_str(arg: cst.Arg) -> str | None:
    """Return the string representation of a simple literal argument value."""
    if isinstance(arg.value, cst.Name):
        return arg.value.value
    if isinstance(arg.value, (cst.SimpleString, cst.ConcatenatedString, cst.FormattedString)):
        return arg.value.evaluated_value if hasattr(arg.value, "evaluated_value") else None
    return None


def _keyword_name(arg: cst.Arg) -> str | None:
    """Return the keyword name of an argument, or None for positional."""
    if arg.keyword is not None:
        return arg.keyword.value
    return None


def _remove_arg_by_keyword(
    args: Sequence[cst.Arg], keyword: str
) -> tuple[list[cst.Arg], cst.Arg | None]:
    """Remove an argument from a list by keyword name, returning the remaining args and removed arg."""
    remaining: list[cst.Arg] = []
    removed: cst.Arg | None = None
    for arg in args:
        if _keyword_name(arg) == keyword and removed is None:
            removed = arg
        else:
            remaining.append(arg)
    return remaining, removed


def _fix_trailing_commas(args: list[cst.Arg]) -> list[cst.Arg]:
    """Ensure the last argument has no trailing comma."""
    if not args:
        return args
    fixed = list(args)
    last = fixed[-1]
    if isinstance(last.comma, cst.Comma):
        fixed[-1] = last.with_changes(comma=cst.MaybeSentinel.DEFAULT)
    return fixed


# ── 1. @validator → @field_validator ───────────────────────────────────


class ValidatorToFieldValidatorTransformer(cst.CSTTransformer):
    """Rewrite ``@validator(...)`` to ``@field_validator(...)``
    and add ``@classmethod`` if missing.  Converts ``pre=True``
    to ``mode='before'`` and removes ``always=True``.
    """

    def __init__(self) -> None:
        super().__init__()
        self._import_validator = False
        self._needs_field_validator_import = False

    # --- Import rewriting ---

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        if m.matches(node, m.ImportFrom(module=m.Attribute(value=m.Name("pydantic")) | m.Name("pydantic"))) and isinstance(node.names, (list, tuple)):
            for alias in node.names:
                if isinstance(alias, cst.ImportAlias) and m.matches(alias.name, m.Name("validator")):
                    self._import_validator = True

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        if not m.matches(updated_node, m.ImportFrom(module=m.Attribute(value=m.Name("pydantic")) | m.Name("pydantic"))):
            return updated_node
        if not isinstance(updated_node.names, (list, tuple)):
            return updated_node

        new_names: list[cst.ImportAlias] = []
        changed = False
        for alias in updated_node.names:
            if isinstance(alias, cst.ImportAlias) and m.matches(alias.name, m.Name("validator")):
                new_names.append(alias.with_changes(name=cst.Name("field_validator")))
                changed = True
                self._needs_field_validator_import = True
            else:
                new_names.append(alias)

        if changed:
            return updated_node.with_changes(names=new_names)
        return updated_node

    # --- Decorator rewriting ---

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        new_decorators: list[cst.Decorator] = []
        has_validator = False
        has_classmethod = False

        for dec in updated_node.decorators:
            # Check for @classmethod already present
            if m.matches(dec, m.Decorator(decorator=m.Name("classmethod"))):
                has_classmethod = True

            # Check for @validator(...)
            if m.matches(dec, m.Decorator(decorator=m.Call(func=m.Name("validator")))):
                has_validator = True
                call = dec.decorator
                assert isinstance(call, cst.Call)

                new_args, pre_arg = _remove_arg_by_keyword(list(call.args), "pre")
                new_args, _ = _remove_arg_by_keyword(new_args, "always")

                # If pre=True was present, add mode='before'
                if pre_arg is not None:
                    val = _arg_value_str(pre_arg)
                    if val == "True":
                        mode_arg = cst.Arg(
                            keyword=cst.Name("mode"),
                            value=cst.SimpleString("'before'"),
                            equal=cst.AssignEqual(
                                whitespace_before=cst.SimpleWhitespace(""),
                                whitespace_after=cst.SimpleWhitespace(""),
                            ),
                        )
                        new_args.append(mode_arg)

                new_args = _fix_trailing_commas(new_args)

                new_call = call.with_changes(
                    func=cst.Name("field_validator"),
                    args=new_args,
                )
                new_decorators.append(dec.with_changes(decorator=new_call))
                continue

            new_decorators.append(dec)

        if not has_validator:
            return updated_node

        # Add @classmethod after the @field_validator decorator if not already present
        if not has_classmethod:
            classmethod_dec = cst.Decorator(
                decorator=cst.Name("classmethod"),
                leading_lines=[],
            )
            # Insert @classmethod right after @field_validator
            final_decorators: list[cst.Decorator] = []
            for dec in new_decorators:
                final_decorators.append(dec)
                if m.matches(dec, m.Decorator(decorator=m.Call(func=m.Name("field_validator")))):
                    final_decorators.append(classmethod_dec)
            new_decorators = final_decorators

        return updated_node.with_changes(decorators=new_decorators)


# ── 2. @root_validator → @model_validator ──────────────────────────────


class RootValidatorToModelValidatorTransformer(cst.CSTTransformer):
    """Rewrite ``@root_validator`` to ``@model_validator(mode='after')``.

    For ``@root_validator(pre=True)`` → ``@model_validator(mode='before')``.
    Adjusts the import statement accordingly.
    """

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        if not m.matches(updated_node, m.ImportFrom(module=m.Attribute(value=m.Name("pydantic")) | m.Name("pydantic"))):
            return updated_node
        if not isinstance(updated_node.names, (list, tuple)):
            return updated_node

        new_names: list[cst.ImportAlias] = []
        changed = False
        for alias in updated_node.names:
            if isinstance(alias, cst.ImportAlias) and m.matches(alias.name, m.Name("root_validator")):
                new_names.append(alias.with_changes(name=cst.Name("model_validator")))
                changed = True
            else:
                new_names.append(alias)

        if changed:
            return updated_node.with_changes(names=new_names)
        return updated_node

    def leave_Decorator(
        self, original_node: cst.Decorator, updated_node: cst.Decorator
    ) -> cst.Decorator:
        # Handle bare @root_validator (no parens)
        if m.matches(updated_node, m.Decorator(decorator=m.Name("root_validator"))):
            new_call = cst.Call(
                func=cst.Name("model_validator"),
                args=[
                    cst.Arg(
                        keyword=cst.Name("mode"),
                        value=cst.SimpleString('"after"'),
                        equal=cst.AssignEqual(
                            whitespace_before=cst.SimpleWhitespace(""),
                            whitespace_after=cst.SimpleWhitespace(""),
                        ),
                    )
                ],
            )
            return updated_node.with_changes(decorator=new_call)

        # Handle @root_validator(...)
        if m.matches(updated_node, m.Decorator(decorator=m.Call(func=m.Name("root_validator")))):
            call = updated_node.decorator
            assert isinstance(call, cst.Call)

            new_args, pre_arg = _remove_arg_by_keyword(list(call.args), "pre")
            mode_value = '"after"'
            if pre_arg is not None:
                val = _arg_value_str(pre_arg)
                if val == "True":
                    mode_value = '"before"'

            # Remove skip_on_failure and other v1-only kwargs
            new_args, _ = _remove_arg_by_keyword(new_args, "skip_on_failure")

            mode_arg = cst.Arg(
                keyword=cst.Name("mode"),
                value=cst.SimpleString(mode_value),
                equal=cst.AssignEqual(
                    whitespace_before=cst.SimpleWhitespace(""),
                    whitespace_after=cst.SimpleWhitespace(""),
                ),
            )
            new_args.append(mode_arg)
            new_args = _fix_trailing_commas(new_args)

            new_call = call.with_changes(
                func=cst.Name("model_validator"),
                args=new_args,
            )
            return updated_node.with_changes(decorator=new_call)

        return updated_node


# ── 3. class Config → model_config = ConfigDict(...) ───────────────────


# Mapping of V1 Config attribute names to V2 ConfigDict key names
_CONFIG_ATTR_RENAMES: dict[str, str] = {
    "orm_mode": "from_attributes",
    "allow_population_by_field_name": "populate_by_name",
    "validate_all": "validate_default",
    "anystr_strip_whitespace": "str_strip_whitespace",
    "min_anystr_length": "str_min_length",
    "max_anystr_length": "str_max_length",
    "schema_extra": "json_schema_extra",
}

# Mapping for V1 values that need rewriting (e.g. Extra.forbid → "forbid")
_CONFIG_VALUE_RENAMES: dict[str, dict[str, str]] = {
    "extra": {
        "Extra.forbid": '"forbid"',
        "Extra.allow": '"allow"',
        "Extra.ignore": '"ignore"',
    }
}


class ConfigClassToModelConfigTransformer(cst.CSTTransformer):
    """Convert inner ``class Config:`` blocks to ``model_config = ConfigDict(...)``."""

    def __init__(self) -> None:
        super().__init__()
        self._class_depth = 0
        self._inside_basemodel = False
        self._add_configdict_import = False

    def visit_ClassDef(self, node: cst.ClassDef) -> bool | None:
        self._class_depth += 1
        # Check if this is likely a BaseModel subclass (heuristic: has base classes)
        if self._class_depth == 1 and node.bases:
            self._inside_basemodel = True
        return True

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef | cst.FlattenSentinel[cst.BaseCompoundStatement]:
        self._class_depth -= 1
        if self._class_depth == 0:
            self._inside_basemodel = False

        # Only transform inner Config class
        if self._class_depth < 1 or not self._inside_basemodel:
            return updated_node

        if updated_node.name.value != "Config":
            return updated_node

        # Extract attribute assignments from the Config body
        config_args: list[cst.Arg] = []
        body = updated_node.body
        if isinstance(body, cst.IndentedBlock):
            for stmt in body.body:
                if isinstance(stmt, cst.SimpleStatementLine):
                    for item in stmt.body:
                        if isinstance(item, cst.AnnAssign) and isinstance(item.target, cst.Name):
                            # e.g., orm_mode: bool = True (treated same as simple assign)
                            attr_name = item.target.value
                            v2_name = _CONFIG_ATTR_RENAMES.get(attr_name, attr_name)
                            if item.value is not None:
                                val_node = item.value
                                config_args.append(
                                    cst.Arg(
                                        keyword=cst.Name(v2_name),
                                        value=val_node,
                                        equal=cst.AssignEqual(
                                            whitespace_before=cst.SimpleWhitespace(""),
                                            whitespace_after=cst.SimpleWhitespace(""),
                                        ),
                                    )
                                )
                        elif isinstance(item, cst.Assign) and len(item.targets) == 1:
                            target = item.targets[0].target
                            if isinstance(target, cst.Name):
                                attr_name = target.value
                                v2_name = _CONFIG_ATTR_RENAMES.get(attr_name, attr_name)

                                value = item.value
                                # Rewrite known value transformations
                                if attr_name in _CONFIG_VALUE_RENAMES:
                                    # Check if the value matches a known rename
                                    val_str = None
                                    if isinstance(value, cst.Attribute):
                                        val_str = f"{value.value.value}.{value.attr.value}" if isinstance(value.value, cst.Name) else None
                                    if val_str and val_str in _CONFIG_VALUE_RENAMES[attr_name]:
                                        value = cst.SimpleString(
                                            _CONFIG_VALUE_RENAMES[attr_name][val_str]
                                        )

                                config_args.append(
                                    cst.Arg(
                                        keyword=cst.Name(v2_name),
                                        value=value,
                                        equal=cst.AssignEqual(
                                            whitespace_before=cst.SimpleWhitespace(""),
                                            whitespace_after=cst.SimpleWhitespace(""),
                                        ),
                                    )
                                )

        if not config_args:
            return updated_node

        config_args = _fix_trailing_commas(config_args)
        self._add_configdict_import = True

        # Build: model_config = ConfigDict(...)
        config_dict_call = cst.Call(
            func=cst.Name("ConfigDict"),
            args=config_args,
        )
        assign = cst.SimpleStatementLine(
            body=[
                cst.Assign(
                    targets=[cst.AssignTarget(target=cst.Name("model_config"))],
                    value=config_dict_call,
                )
            ],
            leading_lines=updated_node.leading_lines if hasattr(updated_node, "leading_lines") else [],
        )

        return cst.FlattenSentinel([assign])

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        """Add ConfigDict to pydantic imports and remove Extra if present."""
        if not m.matches(updated_node, m.ImportFrom(module=m.Attribute(value=m.Name("pydantic")) | m.Name("pydantic"))):
            return updated_node
        if not isinstance(updated_node.names, (list, tuple)):
            return updated_node

        has_configdict = any(
            isinstance(alias, cst.ImportAlias) and m.matches(alias.name, m.Name("ConfigDict"))
            for alias in updated_node.names
        )

        new_names: list[cst.ImportAlias] = []
        for alias in updated_node.names:
            # Remove Extra import (no longer needed in v2 for config)
            if isinstance(alias, cst.ImportAlias) and m.matches(alias.name, m.Name("Extra")):
                continue
            new_names.append(alias)

        # Add ConfigDict import if not already present
        if not has_configdict and new_names:
            # Add comma to current last import
            if new_names:
                last = new_names[-1]
                if not isinstance(last.comma, cst.Comma):
                    new_names[-1] = last.with_changes(
                        comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
                    )
            new_names.append(cst.ImportAlias(name=cst.Name("ConfigDict")))

        if new_names != list(updated_node.names):
            return updated_node.with_changes(names=new_names)
        return updated_node


# ── 4. BaseSettings import rewrite ─────────────────────────────────────


class BaseSettingsImportTransformer(cst.CSTTransformer):
    """Rewrite ``from pydantic import BaseSettings`` to
    ``from pydantic_settings import BaseSettings``.
    """

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom | cst.FlattenSentinel[cst.BaseSmallStatement] | cst.RemovalSentinel:
        if not m.matches(updated_node, m.ImportFrom(module=m.Attribute(value=m.Name("pydantic")) | m.Name("pydantic"))):
            return updated_node
        if not isinstance(updated_node.names, (list, tuple)):
            return updated_node

        has_basesettings = any(
            isinstance(alias, cst.ImportAlias) and m.matches(alias.name, m.Name("BaseSettings"))
            for alias in updated_node.names
        )

        if not has_basesettings:
            return updated_node

        # If BaseSettings is the only import, change the module entirely
        if len(updated_node.names) == 1:
            return updated_node.with_changes(
                module=cst.Attribute(value=cst.Name("pydantic_settings"), attr=cst.Name("BaseSettings"))
                if False  # We actually want to keep `from X import Y` form
                else cst.Name("pydantic_settings"),
            )

        # If there are other imports, remove BaseSettings and we would need
        # to add a new import line — but since we can only return a single node
        # from leave_ImportFrom, we remove BaseSettings and let a module-level
        # pass handle the new import.  For simplicity, just do the removal here.
        remaining: list[cst.ImportAlias] = [
            alias for alias in updated_node.names
            if not (isinstance(alias, cst.ImportAlias) and m.matches(alias.name, m.Name("BaseSettings")))
        ]
        remaining = list(remaining)
        # Fix trailing comma on new last element
        if remaining:
            last = remaining[-1]
            if isinstance(last.comma, cst.Comma):
                remaining[-1] = last.with_changes(comma=cst.MaybeSentinel.DEFAULT)

        return updated_node.with_changes(names=remaining)

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        """Add ``from pydantic_settings import BaseSettings`` if we removed it
        from a pydantic import that had other names."""
        # Check if the original had BaseSettings in a multi-import from pydantic
        original_had_basesettings_multi = False
        for stmt in original_node.body:
            if isinstance(stmt, cst.SimpleStatementLine):
                for item in stmt.body:
                    if (
                        isinstance(item, cst.ImportFrom)
                        and m.matches(
                            item, m.ImportFrom(module=m.Attribute(value=m.Name("pydantic")) | m.Name("pydantic"))
                        )
                        and isinstance(item.names, (list, tuple))
                        and len(item.names) > 1
                    ):
                        for alias in item.names:
                            if isinstance(alias, cst.ImportAlias) and m.matches(alias.name, m.Name("BaseSettings")):
                                original_had_basesettings_multi = True

        if not original_had_basesettings_multi:
            return updated_node

        # Check if pydantic_settings import already exists
        for stmt in updated_node.body:
            if isinstance(stmt, cst.SimpleStatementLine):
                for item in stmt.body:
                    if isinstance(item, cst.ImportFrom) and m.matches(
                        item, m.ImportFrom(module=m.Name("pydantic_settings"))
                    ):
                        return updated_node

        # Insert new import after the pydantic import line
        new_body: list[cst.BaseStatement | cst.SimpleStatementLine] = []
        inserted = False
        for stmt in updated_node.body:
            new_body.append(stmt)
            if not inserted and isinstance(stmt, cst.SimpleStatementLine):
                for item in stmt.body:
                    if isinstance(item, cst.ImportFrom) and m.matches(
                        item, m.ImportFrom(module=m.Attribute(value=m.Name("pydantic")) | m.Name("pydantic"))
                    ):
                        new_import = cst.SimpleStatementLine(
                            body=[
                                cst.ImportFrom(
                                    module=cst.Name("pydantic_settings"),
                                    names=[cst.ImportAlias(name=cst.Name("BaseSettings"))],
                                )
                            ]
                        )
                        new_body.append(new_import)
                        inserted = True
                        break

        if inserted:
            return updated_node.with_changes(body=new_body)
        return updated_node


# ── 5. Method renames (.dict() → .model_dump(), etc.) ──────────────────


# Mapping of old method names to new method names
_METHOD_RENAMES: dict[str, str] = {
    "dict": "model_dump",
    "json": "model_dump_json",
    "parse_obj": "model_validate",
    "parse_raw": "model_validate_json",
    "schema": "model_json_schema",
    "copy": "model_copy",
    "construct": "model_construct",
}


class MethodRenameTransformer(cst.CSTTransformer):
    """Rename deprecated BaseModel method calls to their v2 equivalents.

    Handles both instance calls (``obj.dict()``) and class calls
    (``Model.parse_obj(data)``).
    """

    def leave_Call(
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.Call:
        # Match obj.method_name(...) pattern
        if not isinstance(updated_node.func, cst.Attribute):
            return updated_node

        attr = updated_node.func
        old_name = attr.attr.value

        if old_name in _METHOD_RENAMES:
            new_name = _METHOD_RENAMES[old_name]
            new_attr = attr.with_changes(attr=cst.Name(new_name))
            return updated_node.with_changes(func=new_attr)

        return updated_node


# ── 6. Field(regex=...) → Field(pattern=...) ──────────────────────────


class FieldRegexToPatternTransformer(cst.CSTTransformer):
    """Rename ``regex`` keyword argument to ``pattern`` in ``Field()`` calls."""

    def leave_Call(
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.Call:
        # Match Field(...) calls
        if not m.matches(updated_node, m.Call(func=m.Name("Field"))):
            return updated_node

        new_args: list[cst.Arg] = []
        changed = False
        for arg in updated_node.args:
            if _keyword_name(arg) == "regex":
                new_args.append(arg.with_changes(keyword=cst.Name("pattern")))
                changed = True
            elif _keyword_name(arg) == "min_items":
                new_args.append(arg.with_changes(keyword=cst.Name("min_length")))
                changed = True
            elif _keyword_name(arg) == "max_items":
                new_args.append(arg.with_changes(keyword=cst.Name("max_length")))
                changed = True
            else:
                new_args.append(arg)

        if changed:
            return updated_node.with_changes(args=new_args)
        return updated_node


# ── 7. GenericModel → BaseModel ────────────────────────────────────────


class GenericModelToBaseModelTransformer(cst.CSTTransformer):
    """Replace ``GenericModel`` inheritance with ``BaseModel``.

    Changes ``from pydantic.generics import GenericModel`` to
    ``from pydantic import BaseModel`` and updates the class base.
    """

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        # Match: from pydantic.generics import GenericModel
        if (
            m.matches(
                updated_node,
                m.ImportFrom(
                    module=m.Attribute(value=m.Name("pydantic"), attr=m.Name("generics"))
                ),
            )
            and isinstance(updated_node.names, (list, tuple))
        ):
            for alias in updated_node.names:
                if isinstance(alias, cst.ImportAlias) and m.matches(alias.name, m.Name("GenericModel")):
                    return updated_node.with_changes(
                        module=cst.Name("pydantic"),
                        names=[cst.ImportAlias(name=cst.Name("BaseModel"))],
                    )
        return updated_node

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        """Replace GenericModel in base classes with BaseModel."""
        if not updated_node.bases:
            return updated_node

        new_bases: list[cst.Arg] = []
        changed = False
        for base in updated_node.bases:
            if m.matches(base, m.Arg(value=m.Name("GenericModel"))):
                new_bases.append(base.with_changes(value=cst.Name("BaseModel")))
                changed = True
            else:
                new_bases.append(base)

        if changed:
            return updated_node.with_changes(bases=new_bases)
        return updated_node
