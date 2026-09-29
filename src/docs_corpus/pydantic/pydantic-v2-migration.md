# Pydantic V1 to V2 Migration Guide

This document covers common breaking changes and migration recipes when upgrading codebases from Pydantic V1 to Pydantic V2.

## validator-to-field-validator

### Changes to `@validator`

In Pydantic V1, `@validator` was used for field-level validation. In Pydantic V2, `@validator` is deprecated and replaced by `@field_validator`.

Key differences:
- Import changed from `from pydantic import validator` to `from pydantic import field_validator`.
- `@field_validator` requires `@classmethod` decorator (or behaves as a classmethod).
- The signature changes: the first parameter is `cls`, followed by `v` (the value to validate), and optionally `info: ValidationInfo` instead of `values` dictionary.
- In V2, `mode='before'` or `mode='after'` (default is `'after'`) replaces `pre=True` (use `mode='before'`) and `always=True`.

#### V1 Example:
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

#### V2 Migration:
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

## root-validator-to-model-validator

### Changes to `@root_validator`

In Pydantic V1, `@root_validator` was used for multi-field and model-level validation. In Pydantic V2, `@root_validator` is deprecated in favor of `@model_validator`.

Key differences:
- Import changed from `from pydantic import root_validator` to `from pydantic import model_validator`.
- For validation after fields are processed, use `@model_validator(mode='after')`. The method takes `self` and returns `self`.
- For validation before fields are processed, use `@model_validator(mode='before')`. The method takes `cls, data` as a `@classmethod` and returns data (typically a dict).
- `pre=True` from V1 maps to `mode='before'`.

#### V1 Example:
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

#### V2 Migration:
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

## config-class-to-model-config

### Inner `Config` Class to `model_config = ConfigDict(...)`

In Pydantic V1, model configuration was specified using an inner `class Config:`. In Pydantic V2, this is replaced by setting `model_config = ConfigDict(...)` on the class.

Key attribute renames:
- `orm_mode = True` -> `from_attributes = True`
- `allow_population_by_field_name = True` -> `populate_by_name = True`
- `validate_all = True` -> `validate_default = True`
- `anystr_strip_whitespace = True` -> `str_strip_whitespace = True`
- `min_anystr_length = N` -> `str_min_length = N`
- `max_anystr_length = N` -> `str_max_length = N`
- `schema_extra = dict_or_callable` -> `json_schema_extra = dict_or_callable`
- `extra = Extra.forbid` -> `extra = 'forbid'`

#### V1 Example:
```python
from pydantic import BaseModel, Extra

class Item(BaseModel):
    id: int
    name: str

    class Config:
        orm_mode = True
        extra = Extra.forbid
        allow_population_by_field_name = True
```

#### V2 Migration:
```python
from pydantic import BaseModel, ConfigDict

class Item(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        populate_by_name=True,
    )
```

## basemodel-methods-v2

### Renamed BaseModel Methods and Functions

Pydantic V2 renamed methods on `BaseModel` to avoid collision with field names:
- `.dict()` -> `.model_dump()`
- `.json()` -> `.model_dump_json()`
- `.parse_obj(obj)` -> `.model_validate(obj)`
- `.parse_raw(str_bytes)` -> `.model_validate_json(str_bytes)`
- `.schema()` -> `.model_json_schema()`
- `.copy()` -> `.model_copy()`
- `.construct()` -> `.model_construct()`

#### V1 Example:
```python
data = user.dict(exclude_unset=True)
json_str = user.json()
loaded = UserModel.parse_obj(data)
```

#### V2 Migration:
```python
data = user.model_dump(exclude_unset=True)
json_str = user.model_dump_json()
loaded = UserModel.model_validate(data)
```

## field-regex-to-pattern

### Changes in `Field()` Arguments

In Pydantic V1, regex validation on fields used `Field(regex="^pattern$")`. In Pydantic V2, this parameter was renamed to `pattern`.
Additionally:
- `min_items` -> `min_length`
- `max_items` -> `max_length`
- `const=True` is removed; use `typing.Literal` instead.

#### V1 Example:
```python
from pydantic import BaseModel, Field

class Account(BaseModel):
    code: str = Field(..., regex="^[A-Z]{3}$", min_items=1)
```

#### V2 Migration:
```python
from pydantic import BaseModel, Field

class Account(BaseModel):
    code: str = Field(..., pattern="^[A-Z]{3}$", min_length=1)
```

## genericmodel-deprecated

### Generic Models in Pydantic V2

In Pydantic V1, generic models had to inherit from `pydantic.generics.GenericModel`. In Pydantic V2, `GenericModel` is deprecated. Simply inherit directly from `typing.Generic[T]` and `pydantic.BaseModel`.

#### V1 Example:
```python
from typing import Generic, TypeVar
from pydantic.generics import GenericModel

T = TypeVar("T")

class ResponseEnvelope(GenericModel, Generic[T]):
    data: T
```

#### V2 Migration:
```python
from typing import Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")

class ResponseEnvelope(BaseModel, Generic[T]):
    data: T
```

## basesettings-moved-to-pydantic-settings

### `BaseSettings` Moved to Separate Package

In Pydantic V2, `BaseSettings` is no longer part of core `pydantic`. It has been extracted into `pydantic-settings`.

#### V1 Example:
```python
from pydantic import BaseSettings

class Settings(BaseSettings):
    api_key: str
```

#### V2 Migration:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    api_key: str
```
