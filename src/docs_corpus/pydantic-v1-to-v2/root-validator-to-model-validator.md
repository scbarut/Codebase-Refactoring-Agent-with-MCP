# @root_validator → @model_validator

## Summary

In Pydantic V1, `@root_validator` was used for multi-field and model-level
validation. In Pydantic V2, `@root_validator` is deprecated in favor of
`@model_validator`.

## Key Changes

- **Import**: `from pydantic import root_validator` → `from pydantic import model_validator`
- **After mode** (default): `@model_validator(mode='after')` — method takes `self`,
  returns `self`.
- **Before mode**: `@model_validator(mode='before')` — `@classmethod` that takes `cls`
  and `data` (typically a dict), returns data.
- **V1 mapping**: `pre=True` → `mode='before'`; no `pre` or `pre=False` → `mode='after'`.

## V1 Example

```python
from pydantic import BaseModel, root_validator

class Rectangle(BaseModel):
    width: float
    height: float

    @root_validator
    def check_dimensions(cls, values):
        w, h = values.get("width"), values.get("height")
        if w is not None and h is not None and w <= 0:
            raise ValueError("width must be positive")
        return values
```

## V2 Migration

```python
from pydantic import BaseModel, model_validator

class Rectangle(BaseModel):
    width: float
    height: float

    @model_validator(mode="after")
    def check_dimensions(self) -> "Rectangle":
        if self.width <= 0:
            raise ValueError("width must be positive")
        return self
```

## References

- [Pydantic V2 Migration Guide — Root Validators](https://docs.pydantic.dev/latest/migration/#root-validators)
