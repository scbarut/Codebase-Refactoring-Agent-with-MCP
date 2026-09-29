# Lightweight doc retrieval: pre-indexed corpus with constrained web fallback

`mcp-server-docs` serves a pre-indexed Doc Corpus (markdown files of official migration guides) as the primary source. When a test traceback doesn't match any indexed content, it falls back to web search via Tavily API or DuckDuckGo + Trafilatura for noise-free markdown extraction.

The web fallback is deliberately constrained: top-3 relevant chunks, max 1500 tokens total, matched against the specific test traceback. No headless browsers (Playwright/Selenium) — they're heavy, flaky, and the content we need (migration docs, Stack Overflow answers) is extractable without JavaScript rendering.

We considered full web scraping (powerful but fragile and slow) and pure pre-indexed (reliable but can't handle edge cases or newly published docs). The constrained hybrid keeps the context lean while covering the long tail.
