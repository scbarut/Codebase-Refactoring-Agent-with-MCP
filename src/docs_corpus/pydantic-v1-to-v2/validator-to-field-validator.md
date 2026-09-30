# @validator → @field_validator

## Summary

In Pydantic V1, `@validator` was used for field-level validation. In Pydantic V2,
`@validator` is deprecated and replaced by `@field_validator`.

## Key Changes

- **Import**: `from pydantic import validator` → `from pydantic import field_validator`
- **Classmethod**: `@field_validator` requires explicit `@classmethod` decorator
  (or behaves as a classmethod by default).
- **Signature**: The first parameter is `cls`, followed by `v` (the value), and
  optionally `info: ValidationInfo` instead of the `values` dictionary.
- **Mode**: `pre=True` → `mode='before'`, `pre=False` (default) → `mode='after'` (default).
- **Always**: `always=True` is removed; use `validate_default=True` in `model_config` instead.

## V1 Example

```python
from pydantic import BaseModel, validator

class UserModel(BaseModel):
    name: str

    @validator("name")
    def validate_name(cls, v):
        if not v.strip():
            raise ValueError("name cannot be empty")
        return v.title()
```

## V2 Migration

```python
from pydantic import BaseModel, field_validator

class UserModel(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be empty")
        return v.title()
```

## With `pre=True` → `mode='before'`

### V1
```python
@validator("name", pre=True)
def coerce_name(cls, v):
    return str(v)
```

### V2
```python
@field_validator("name", mode='before')
@classmethod
def coerce_name(cls, v: Any) -> str:
    return str(v)
```

## References

- [Pydantic V2 Migration Guide — Validators](https://docs.pydantic.dev/latest/migration/#validators)
