# Field(regex=...) → Field(pattern=...)

## Summary

In Pydantic V1, regex validation on string fields used `Field(regex="^pattern$")`.
In Pydantic V2, this parameter was renamed to `pattern`.

## Additional Field Argument Renames

| V1                 | V2               |
| -------------------- | ------------------ |
| `regex="..."`      | `pattern="..."`  |
| `min_items=N`      | `min_length=N`   |
| `max_items=N`      | `max_length=N`   |
| `const=True`       | Use `typing.Literal` instead |

## V1 Example

```python
from pydantic import BaseModel, Field

class Account(BaseModel):
    code: str = Field(..., regex="^[A-Z]{3}$", min_items=1)
```

## V2 Migration

```python
from pydantic import BaseModel, Field

class Account(BaseModel):
    code: str = Field(..., pattern="^[A-Z]{3}$", min_length=1)
```

## References

- [Pydantic V2 Migration Guide — Field Changes](https://docs.pydantic.dev/latest/migration/#changes-to-field)
