# Pydantic V1 to V2 Doc Corpus

This directory contains pre-indexed markdown sections from the official Pydantic v2 migration guide.
Each file corresponds to a `doc_ref` value used in the rule set at `src/rules/pydantic_v1_to_v2.yaml`.

## Sections

- `validator-to-field-validator.md` — `@validator` → `@field_validator`
- `root-validator-to-model-validator.md` — `@root_validator` → `@model_validator`
- `config-class-to-model-config.md` — `class Config` → `model_config = ConfigDict(...)`
- `basemodel-methods-v2.md` — Renamed BaseModel methods (`.dict()`, `.json()`, etc.)
- `field-regex-to-pattern.md` — `Field(regex=...)` → `Field(pattern=...)`
- `genericmodel-deprecated.md` — `GenericModel` removed, use `BaseModel + Generic[T]`
- `basesettings-moved-to-pydantic-settings.md` — `BaseSettings` moved to `pydantic-settings`
