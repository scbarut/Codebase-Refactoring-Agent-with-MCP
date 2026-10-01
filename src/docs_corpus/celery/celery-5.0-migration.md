# Celery 5.0 Migration Guide

## Shared Task
In Celery 4 and earlier, tasks were often registered using `@app.task` or the deprecated `from celery.task import task` decorator. In Celery 5.0+:
- Use `from celery import shared_task` and the `@shared_task` decorator.
- This creates reusable tasks independent of specific app instances.

```python
# Before:
from celery.task import task

@task
def add(x, y):
    return x + y

# After:
from celery import shared_task

@shared_task
def add(x, y):
    return x + y
```

## Lowercase Settings
In Celery 5.0, settings must use lowercase names with no prefix:
- `CELERY_BROKER_URL` → `broker_url`
- `CELERY_RESULT_BACKEND` → `result_backend`
- `CELERY_TASK_SERIALIZER` → `task_serializer`
- `CELERY_TIMEZONE` → `timezone`

```python
# Before:
app.conf.update(
    CELERY_BROKER_URL="redis://localhost:6379/0",
    CELERY_RESULT_BACKEND="redis://localhost:6379/0",
)

# After:
app.conf.update(
    broker_url="redis://localhost:6379/0",
    result_backend="redis://localhost:6379/0",
)
```
