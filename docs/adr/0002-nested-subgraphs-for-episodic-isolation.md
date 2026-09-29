# Nested LangGraph sub-graphs for episodic context isolation

Each file in a Migration Job is processed by its own File Sub-graph (rewrite → test → Self-Healing Loop). The Orchestration Graph spawns these sub-graphs sequentially and aggregates their results.

We considered a flat graph with conditional edges (simpler to build, but test tracebacks and healing context from file A would pollute file B's prompt, degrading LLM performance and risking hallucinated cross-file imports). Nested sub-graphs keep each file's context window clean and make parallelization a future option without architectural change.
