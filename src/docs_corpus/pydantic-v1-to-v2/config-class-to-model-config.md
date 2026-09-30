# class Config → model_config = ConfigDict(...)

## Summary

In Pydantic V1, model configuration was specified using an inner `class Config:`.
In Pydantic V2, this is replaced by setting `model_config = ConfigDict(...)` as a
class variable.

## Key Attribute Renames

| V1 (`class Config`)                   | V2 (`ConfigDict`)           |
| -------------------------------------- | --------------------------- |
| `orm_mode = True`                      | `from_attributes = True`    |
| `allow_population_by_field_name = True`| `populate_by_name = True`   |
| `validate_all = True`                  | `validate_default = True`   |
| `anystr_strip_whitespace = True`       | `str_strip_whitespace = True` |
| `min_anystr_length = N`               | `str_min_length = N`        |
| `max_anystr_length = N`               | `str_max_length = N`        |
| `schema_extra = dict_or_callable`     | `json_schema_extra = ...`   |
| `extra = Extra.forbid`                | `extra = 'forbid'`          |

## V1 Example

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

## V2 Migration

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

## References

- [Pydantic V2 Migration Guide — Config](https://docs.pydantic.dev/latest/migration/#changes-to-config)
