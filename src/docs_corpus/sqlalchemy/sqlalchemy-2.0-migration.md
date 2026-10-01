# SQLAlchemy 2.0 Migration Guide

## Declarative Base
In SQLAlchemy 1.4 and earlier, `declarative_base()` was commonly imported from `sqlalchemy.ext.declarative`. In SQLAlchemy 2.0:
- Use `from sqlalchemy.orm import DeclarativeBase` and define `class Base(DeclarativeBase): pass`.
- Or use `from sqlalchemy.orm import declarative_base` as a direct drop-in replacement.

Example:
```python
# Before (1.4):
from sqlalchemy.ext.declarative import declarative_base
Base = declarative_base()

# After (2.0):
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

## Session Execute Select
SQLAlchemy 2.0 replaces 1.x style query syntax with `select()` constructs executed against the session:

```python
# Before (1.x):
users = session.query(User).filter_by(name="Alice").all()

# After (2.0):
from sqlalchemy import select
stmt = select(User).filter_by(name="Alice")
users = session.execute(stmt).scalars().all()
```

## Session Get
In SQLAlchemy 1.x, getting an instance by primary key used `session.query(Model).get(id)` or `query.get(id)`.
In SQLAlchemy 2.0, use `session.get(Model, id)` directly:

```python
# Before:
user = session.query(User).get(1)

# After:
user = session.get(User, 1)
```
