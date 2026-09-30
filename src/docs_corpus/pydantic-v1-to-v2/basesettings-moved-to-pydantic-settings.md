# BaseSettings Moved to pydantic-settings

## Summary

In Pydantic V2, `BaseSettings` is no longer part of core `pydantic`. It has been
extracted into the separate `pydantic-settings` package.

## Key Changes

- **Install**: `pip install pydantic-settings`
- **Import**: `from pydantic import BaseSettings` → `from pydantic_settings import BaseSettings`
- **SettingsConfigDict**: Use `SettingsConfigDict` from `pydantic_settings` instead of
  `class Config` for settings-specific configuration.

## V1 Example

```python
from pydantic import BaseSettings

class Settings(BaseSettings):
    api_key: str
```

## V2 Migration

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    api_key: str
```

## References

- [Pydantic V2 Migration Guide — BaseSettings](https://docs.pydantic.dev/latest/migration/#basesettings)
- [pydantic-settings Documentation](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
