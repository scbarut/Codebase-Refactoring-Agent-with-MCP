# 05: Pydantic v1→v2 Migration Rule Set and Doc Corpus

**What to build:** A complete Migration Rule Set for the Pydantic v1 → v2 transition: YAML rule definitions for mechanically-rewritable patterns, Python `CSTTransformer` classes for complex patterns, and a pre-indexed Doc Corpus of the official Pydantic v2 migration guide. When fed sample Pydantic v1 source files through `mcp-server-ast`, the rules produce correct v2 output for all covered patterns. Each rule carries a `doc_ref` and a base risk category.

**Blocked by:** 03 (mcp-server-ast)

**Status:** done

- [x] YAML rule file at `src/rules/pydantic_v1_to_v2.yaml` with entries for all common mechanical rewrites
- [x] Rules cover at minimum: `@validator` → `@field_validator`, `@root_validator` → `@model_validator`, `class Config` → `model_config = ConfigDict(...)`, `from pydantic import BaseSettings` → `from pydantic_settings import BaseSettings`, `.dict()` → `.model_dump()`, `.json()` → `.model_dump_json()`, `.parse_obj()` → `.model_validate()`, `.parse_raw()` → `.model_validate_json()`
- [x] Each YAML rule entry includes: `old_qualified_name`, `new_qualified_name`, `risk` (LOW/MEDIUM/HIGH), `doc_ref`, and optionally `transformer_class` (dotted path to a Python CSTTransformer)
- [x] Python `CSTTransformer` classes for complex patterns (e.g., `Config` class → `model_config` dict conversion, `@validator` argument rewriting with `mode='before'`/`mode='wrap'`)
- [x] Doc Corpus at `src/docs_corpus/pydantic-v1-to-v2/` with markdown sections extracted from the official Pydantic v2 migration guide, each section addressable by the `doc_ref` in the rules
- [x] Tests feed sample v1 files (covering each rule) through `mcp-server-ast` and verify correct v2 output
- [x] Tests verify that formatting and comments are preserved in the rewritten output

