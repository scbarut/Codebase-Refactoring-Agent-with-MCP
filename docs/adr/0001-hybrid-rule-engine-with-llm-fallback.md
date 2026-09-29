# Hybrid rule engine: declarative rules with LLM fallback

Well-known migration patterns (Pydantic v1→v2, SQLAlchemy 1.4→2.0) are handled by declarative Migration Rules applied via libcst transformers — deterministic, fast, no LLM call. When a deprecated usage doesn't match any rule, the agent falls back to the LLM with relevant Doc Corpus context.

We considered pure-LLM (more flexible but non-deterministic and expensive for common patterns) and pure-declarative (cheap but can't handle novel patterns). The hybrid gives deterministic speed for the 80% case and LLM flexibility for the tail.

The tiered model routing reinforces this: `gemini-flash-lite` handles basic declarative-guided transforms (simple renames, argument reordering), `gemini-flash` handles complex reasoning (class hierarchy changes, behavioral rewrites). The routing decision is made per-Rewrite based on the Migration Rule's risk category: `LOW` → flash-lite, `MEDIUM`/`HIGH` or no matching rule → flash.
