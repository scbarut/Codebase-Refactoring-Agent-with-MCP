"""Tests for the Pydantic v1→v2 migration rule set.

Tests three seams:
1. Rule loading — YAML rule file loads and has expected structure
2. CSTTransformers — each transformer via apply_transform produces correct v2 output,
   preserves formatting/comments, and is idempotent on already-migrated code
3. Doc corpus resolution — every rule's doc_ref resolves to an existing markdown file
"""

import json
from pathlib import Path

import pytest

from src.mcp_servers.ast_server import create_ast_server
from src.rules.loader import load_rules

# ── Paths ──────────────────────────────────────────────────────────────

RULES_DIR = Path(__file__).resolve().parent.parent / "src" / "rules"
RULES_YAML = RULES_DIR / "pydantic_v1_to_v2.yaml"
DOC_CORPUS_DIR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "docs_corpus"
    / "pydantic-v1-to-v2"
)


# ═══════════════════════════════════════════════════════════════════════
# Seam 1: Rule loading
# ═══════════════════════════════════════════════════════════════════════


class TestRuleLoading:
    """Verify the YAML rule set loads correctly and has all required fields."""

    def test_load_rules_returns_list(self):
        rules = load_rules(RULES_YAML)
        assert isinstance(rules, list)
        assert len(rules) >= 8  # at minimum the 8 rules from the spec

    def test_every_rule_has_required_fields(self):
        rules = load_rules(RULES_YAML)
        required = {"id", "old_qualified_name", "new_qualified_name", "risk", "doc_ref"}
        for rule in rules:
            missing = required - set(rule.keys())
            assert not missing, f"Rule {rule.get('id', '?')} missing: {missing}"

    def test_risk_values_are_valid(self):
        rules = load_rules(RULES_YAML)
        valid_risks = {"LOW", "MEDIUM", "HIGH"}
        for rule in rules:
            assert rule["risk"] in valid_risks, (
                f"Rule {rule['id']} has invalid risk: {rule['risk']}"
            )

    def test_all_spec_patterns_covered(self):
        """Issue spec lists minimum patterns that must be present."""
        rules = load_rules(RULES_YAML)
        rule_ids = {r["id"] for r in rules}
        expected_ids = {
            "validator-to-field-validator",
            "root-validator-to-model-validator",
            "config-class-to-model-config",
            "basesettings-to-pydantic-settings",
            "dict-to-model-dump",
            "json-to-model-dump-json",
            "parse-obj-to-model-validate",
            "parse-raw-to-model-validate-json",
        }
        missing = expected_ids - rule_ids
        assert not missing, f"Missing rule IDs: {missing}"

    def test_load_rules_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            load_rules(Path("nonexistent.yaml"))

    def test_load_rules_invalid_yaml(self, tmp_path: Path):
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("key: value\n", encoding="utf-8")
        with pytest.raises(ValueError, match="rules"):
            load_rules(bad_file)


# ═══════════════════════════════════════════════════════════════════════
# Seam 2: CSTTransformers (via apply_transform)
# ═══════════════════════════════════════════════════════════════════════


async def _apply(server, file_path: str, transformer_path: str) -> dict:
    """Helper: apply a transformer and return the parsed result dict."""
    result = await server.call_tool(
        "apply_transform",
        {"file_path": file_path, "transformer_path": transformer_path},
    )
    assert not result.is_error, f"apply_transform failed: {result}"
    return json.loads(result.content[0].text)


async def _apply_and_check_idempotent(
    server, tmp_path: Path, v1_code: str, transformer_path: str
) -> str:
    """Apply transformer to v1_code, then re-apply to the output and assert idempotency.

    Returns the v2 code (first pass output).
    """
    # First pass: v1 → v2
    v1_file = tmp_path / "v1_source.py"
    v1_file.write_text(v1_code, encoding="utf-8")
    data1 = await _apply(server, str(v1_file), transformer_path)
    v2_code = data1["transformed_code"]
    assert data1["modified"] is True, "Transformer should modify v1 code"

    # Second pass: v2 → v2 (idempotency)
    v2_file = tmp_path / "v2_source.py"
    v2_file.write_text(v2_code, encoding="utf-8")
    data2 = await _apply(server, str(v2_file), transformer_path)
    assert data2["modified"] is False, (
        f"Transformer is NOT idempotent!\n"
        f"--- Second-pass input ---\n{v2_code}\n"
        f"--- Second-pass output ---\n{data2['transformed_code']}"
    )
    return v2_code


# ── @validator → @field_validator ──────────────────────────────────────


@pytest.mark.asyncio
async def test_validator_to_field_validator(tmp_path: Path):
    server = create_ast_server()
    v1_code = '''\
from pydantic import BaseModel, validator

class UserModel(BaseModel):
    name: str

    # Name must not be blank
    @validator("name")
    def validate_name(cls, v):
        if not v.strip():
            raise ValueError("name cannot be empty")
        return v.title()
'''
    transformer = "src.rules.pydantic_transformers.ValidatorToFieldValidatorTransformer"
    v2 = await _apply_and_check_idempotent(server, tmp_path, v1_code, transformer)

    assert "field_validator" in v2
    assert "@classmethod" in v2
    assert "# Name must not be blank" in v2
    assert "validator" not in v2.split("field_validator")[0].split("\n")[-1]


@pytest.mark.asyncio
async def test_validator_with_pre_true(tmp_path: Path):
    server = create_ast_server()
    v1_code = '''\
from pydantic import BaseModel, validator

class Coerce(BaseModel):
    value: str

    @validator("value", pre=True)
    def coerce_value(cls, v):
        return str(v)
'''
    transformer = "src.rules.pydantic_transformers.ValidatorToFieldValidatorTransformer"
    v2 = await _apply_and_check_idempotent(server, tmp_path, v1_code, transformer)

    assert "field_validator" in v2
    assert "mode='before'" in v2
    assert "pre=True" not in v2


# ── @root_validator → @model_validator ─────────────────────────────────


@pytest.mark.asyncio
async def test_root_validator_to_model_validator(tmp_path: Path):
    server = create_ast_server()
    v1_code = '''\
from pydantic import BaseModel, root_validator

class Rectangle(BaseModel):
    width: float
    height: float

    # Ensure positive dimensions
    @root_validator
    def check_dimensions(cls, values):
        w, h = values.get("width"), values.get("height")
        if w is not None and h is not None and w <= 0:
            raise ValueError("width must be positive")
        return values
'''
    transformer = "src.rules.pydantic_transformers.RootValidatorToModelValidatorTransformer"
    v2 = await _apply_and_check_idempotent(server, tmp_path, v1_code, transformer)

    assert "model_validator" in v2
    assert "root_validator" not in v2
    assert "# Ensure positive dimensions" in v2
    assert 'mode="after"' in v2


@pytest.mark.asyncio
async def test_root_validator_with_pre_true(tmp_path: Path):
    server = create_ast_server()
    v1_code = '''\
from pydantic import BaseModel, root_validator

class PreCheck(BaseModel):
    x: int

    @root_validator(pre=True)
    def pre_check(cls, values):
        return values
'''
    transformer = "src.rules.pydantic_transformers.RootValidatorToModelValidatorTransformer"
    v2 = await _apply_and_check_idempotent(server, tmp_path, v1_code, transformer)

    assert "model_validator" in v2
    assert 'mode="before"' in v2


# ── class Config → model_config ────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_class_to_model_config(tmp_path: Path):
    server = create_ast_server()
    v1_code = '''\
from pydantic import BaseModel, Extra

class Item(BaseModel):
    id: int
    name: str

    # Configuration for the model
    class Config:
        orm_mode = True
        extra = Extra.forbid
        allow_population_by_field_name = True
'''
    transformer = "src.rules.pydantic_transformers.ConfigClassToModelConfigTransformer"
    v1_file = tmp_path / "config_v1.py"
    v1_file.write_text(v1_code, encoding="utf-8")
    data = await _apply(server, str(v1_file), transformer)
    v2 = data["transformed_code"]

    assert data["modified"] is True
    assert "model_config" in v2
    assert "ConfigDict" in v2
    assert "from_attributes" in v2
    assert "populate_by_name" in v2
    assert '"forbid"' in v2
    assert "class Config" not in v2
    # Extra import should be removed, ConfigDict added
    assert "Extra" not in v2


# ── BaseSettings import ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_basesettings_sole_import(tmp_path: Path):
    """BaseSettings as the only import from pydantic."""
    server = create_ast_server()
    v1_code = '''\
from pydantic import BaseSettings

class Settings(BaseSettings):
    api_key: str
'''
    transformer = "src.rules.pydantic_transformers.BaseSettingsImportTransformer"
    v2 = await _apply_and_check_idempotent(server, tmp_path, v1_code, transformer)

    assert "from pydantic_settings import BaseSettings" in v2
    assert "from pydantic import BaseSettings" not in v2


@pytest.mark.asyncio
async def test_basesettings_multi_import(tmp_path: Path):
    """BaseSettings alongside other pydantic imports."""
    server = create_ast_server()
    v1_code = '''\
from pydantic import BaseModel, BaseSettings

class Settings(BaseSettings):
    api_key: str
'''
    transformer = "src.rules.pydantic_transformers.BaseSettingsImportTransformer"
    v1_file = tmp_path / "settings_multi.py"
    v1_file.write_text(v1_code, encoding="utf-8")
    data = await _apply(server, str(v1_file), transformer)
    v2 = data["transformed_code"]

    assert data["modified"] is True
    # BaseModel should remain in pydantic import
    assert "from pydantic import BaseModel" in v2
    # BaseSettings should be in its own pydantic_settings import
    assert "from pydantic_settings import BaseSettings" in v2


# ── Method renames ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_method_rename_dict(tmp_path: Path):
    server = create_ast_server()
    v1_code = '''\
# Serialise user data
data = user.dict(exclude_unset=True)
'''
    transformer = "src.rules.pydantic_transformers.MethodRenameTransformer"
    v2 = await _apply_and_check_idempotent(server, tmp_path, v1_code, transformer)

    assert "model_dump" in v2
    assert ".dict(" not in v2
    assert "# Serialise user data" in v2


@pytest.mark.asyncio
async def test_method_rename_json(tmp_path: Path):
    server = create_ast_server()
    v1_code = '''\
json_str = user.json()
'''
    transformer = "src.rules.pydantic_transformers.MethodRenameTransformer"
    v2 = await _apply_and_check_idempotent(server, tmp_path, v1_code, transformer)

    assert "model_dump_json" in v2
    assert ".json(" not in v2


@pytest.mark.asyncio
async def test_method_rename_parse_obj(tmp_path: Path):
    server = create_ast_server()
    v1_code = '''\
loaded = UserModel.parse_obj(data)
'''
    transformer = "src.rules.pydantic_transformers.MethodRenameTransformer"
    v2 = await _apply_and_check_idempotent(server, tmp_path, v1_code, transformer)

    assert "model_validate" in v2
    assert ".parse_obj(" not in v2


@pytest.mark.asyncio
async def test_method_rename_parse_raw(tmp_path: Path):
    server = create_ast_server()
    v1_code = '''\
raw = UserModel.parse_raw(raw_bytes)
'''
    transformer = "src.rules.pydantic_transformers.MethodRenameTransformer"
    v2 = await _apply_and_check_idempotent(server, tmp_path, v1_code, transformer)

    assert "model_validate_json" in v2
    assert ".parse_raw(" not in v2


# ── Field regex → pattern ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_field_regex_to_pattern(tmp_path: Path):
    server = create_ast_server()
    v1_code = '''\
from pydantic import BaseModel, Field

class Account(BaseModel):
    # Account code must be 3 uppercase letters
    code: str = Field(..., regex="^[A-Z]{3}$", min_items=1)
'''
    transformer = "src.rules.pydantic_transformers.FieldRegexToPatternTransformer"
    v2 = await _apply_and_check_idempotent(server, tmp_path, v1_code, transformer)

    assert "pattern=" in v2
    assert "regex=" not in v2
    assert "min_length=" in v2
    assert "min_items=" not in v2
    assert "# Account code must be 3 uppercase letters" in v2


# ── GenericModel → BaseModel ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_genericmodel_to_basemodel(tmp_path: Path):
    server = create_ast_server()
    v1_code = '''\
from typing import Generic, TypeVar
from pydantic.generics import GenericModel

T = TypeVar("T")

# Generic envelope for API responses
class ResponseEnvelope(GenericModel, Generic[T]):
    data: T
'''
    transformer = "src.rules.pydantic_transformers.GenericModelToBaseModelTransformer"
    v2 = await _apply_and_check_idempotent(server, tmp_path, v1_code, transformer)

    assert "from pydantic import BaseModel" in v2
    assert "GenericModel" not in v2
    assert "BaseModel, Generic[T]" in v2 or "BaseModel,Generic[T]" in v2 or ("BaseModel" in v2 and "Generic[T]" in v2)
    assert "# Generic envelope for API responses" in v2


# ═══════════════════════════════════════════════════════════════════════
# Seam 3: Doc corpus resolution
# ═══════════════════════════════════════════════════════════════════════


class TestDocCorpusResolution:
    """Verify every rule's doc_ref resolves to an existing markdown file."""

    def test_all_doc_refs_resolve(self):
        rules = load_rules(RULES_YAML)
        missing: list[str] = []
        for rule in rules:
            doc_ref = rule["doc_ref"]
            doc_path = DOC_CORPUS_DIR / f"{doc_ref}.md"
            if not doc_path.exists():
                missing.append(f"{rule['id']} → {doc_ref} (expected {doc_path})")
        assert not missing, "Unresolved doc_refs:\n" + "\n".join(missing)

    def test_doc_corpus_files_are_nonempty(self):
        rules = load_rules(RULES_YAML)
        doc_refs = {rule["doc_ref"] for rule in rules}
        for doc_ref in doc_refs:
            doc_path = DOC_CORPUS_DIR / f"{doc_ref}.md"
            content = doc_path.read_text(encoding="utf-8")
            assert len(content.strip()) > 50, (
                f"Doc corpus file {doc_path.name} is too short ({len(content)} chars)"
            )

    def test_doc_corpus_directory_exists(self):
        assert DOC_CORPUS_DIR.is_dir(), f"Doc corpus directory missing: {DOC_CORPUS_DIR}"
