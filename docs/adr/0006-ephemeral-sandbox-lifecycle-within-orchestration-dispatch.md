# Ephemeral Sandbox container lifecycle within Orchestration dispatch

The Sandbox Docker container is provisioned, loaded with dependencies, and destroyed entirely within the execution scope of the Orchestration Graph's file dispatch step via an async context manager (`managed_sandbox`), rather than as discrete graph nodes or serialized LangGraph state.

LangGraph state is serialized to PostgreSQL via `PostgresSaver` across node transitions. Docker `Container` objects cannot be JSON-serialized or pickled into a database checkpointer. Managing container lifespan inside the dispatch seam ensures that live containers never leak into graph state, while guaranteeing deterministic cleanup (`destroy_sandbox`) across all error and completion paths.

If Docker is unavailable or the daemon cannot be reached, the lifecycle degrades gracefully by logging a warning and marking scoped tests as skipped, allowing static AST and LLM rewrites to continue without halting execution.
