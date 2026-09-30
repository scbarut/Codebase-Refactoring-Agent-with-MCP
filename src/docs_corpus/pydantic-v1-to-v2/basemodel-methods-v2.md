# Renamed BaseModel Methods

## Summary

Pydantic V2 renamed several `BaseModel` methods to use the `model_` prefix,
avoiding collisions with field names.

## Method Renames

| V1 Method              | V2 Method                    |
| ----------------------- | ---------------------------- |
| `.dict()`              | `.model_dump()`              |
| `.json()`              | `.model_dump_json()`         |
| `.parse_obj(obj)`      | `.model_validate(obj)`       |
| `.parse_raw(str_bytes)`| `.model_validate_json(str_bytes)` |
| `.schema()`            | `.model_json_schema()`       |
| `.copy()`              | `.model_copy()`              |
| `.construct()`         | `.model_construct()`         |

## V1 Example

```python
data = user.dict(exclude_unset=True)
json_str = user.json()
loaded = UserModel.parse_obj(data)
raw = UserModel.parse_raw('{"name": "test"}')
```

## V2 Migration

```python
data = user.model_dump(exclude_unset=True)
json_str = user.model_dump_json()
loaded = UserModel.model_validate(data)
raw = UserModel.model_validate_json('{"name": "test"}')
```

## References

- [Pydantic V2 Migration Guide — Model Methods](https://docs.pydantic.dev/latest/migration/#changes-to-model-methods)
