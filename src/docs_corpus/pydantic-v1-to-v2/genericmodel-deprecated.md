# GenericModel Deprecated

## Summary

In Pydantic V1, generic models had to inherit from
`pydantic.generics.GenericModel`. In Pydantic V2, `GenericModel` is deprecated.
Simply inherit directly from `pydantic.BaseModel` and `typing.Generic[T]`.

## V1 Example

```python
from typing import Generic, TypeVar
from pydantic.generics import GenericModel

T = TypeVar("T")

class ResponseEnvelope(GenericModel, Generic[T]):
    data: T
```

## V2 Migration

```python
from typing import Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")

class ResponseEnvelope(BaseModel, Generic[T]):
    data: T
```

## References

- [Pydantic V2 Migration Guide — Generic Models](https://docs.pydantic.dev/latest/migration/#generic-models)
