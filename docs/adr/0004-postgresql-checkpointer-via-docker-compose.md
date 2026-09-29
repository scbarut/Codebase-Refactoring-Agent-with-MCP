# PostgreSQL checkpointer via docker-compose

LangGraph state persistence uses PostgresSaver backed by a PostgreSQL instance managed in docker-compose, rather than SQLite.

SQLite would be zero-dependency and simpler for a single-user tool, but PostgreSQL gives production-grade concurrency, crash recovery, and a natural path to multi-user deployments without a backend migration. Since the sandbox already requires Docker, adding a PostgreSQL container to docker-compose is marginal infrastructure cost. The LangGraph checkpoint schema is identical across backends, so this is a topology decision, not a code one.
