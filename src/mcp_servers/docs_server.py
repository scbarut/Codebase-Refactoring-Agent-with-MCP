from __future__ import annotations

import html
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
from mcp.server.mcpserver import MCPServer

from src.core.config import load_config
from src.core.logging import get_logger

logger = get_logger(__name__)


def _slugify(text: str) -> str:
    """Convert heading text to a clean anchor slug.

    Strips markdown formatting, code ticks, symbols, and replaces whitespace/punctuation with hyphens.
    """
    # Remove markdown link syntax [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove backticks and inline formatting characters
    text = re.sub(r"[`*_~]", "", text)
    # Lowercase
    text = text.lower().strip()
    # Replace non-alphanumeric (except underscore and hyphen) with hyphen
    text = re.sub(r"[^\w\-]+", "-", text)
    # Collapse multiple hyphens
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def _extract_tokens(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric and underscore words for BM25."""
    return [t.lower() for t in re.findall(r"[a-zA-Z0-9_]+", text)]


def _count_tokens(text: str) -> int:
    """Count tokens accurately using tiktoken if available, with char heuristic fallback."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # noqa: BLE001
        # Fallback approximation: ~4 characters per token
        chars = len(text)
        return max(1, math.ceil(chars / 4)) if text else 0


def _clean_html_artifacts(text: str) -> str:
    """Convert HTML snippet to clean markdown text without HTML tags and entities."""
    # Remove HTML comments
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # Remove script and style elements
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Replace breaks and paragraph tags with newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</h[1-6]>", "\n\n", text, flags=re.IGNORECASE)
    # Strip remaining HTML tags (opening/closing tags starting with letter or /)
    text = re.sub(r"</?[a-zA-Z][^>]*>", " ", text)
    # Decode HTML entities AFTER stripping tags
    text = html.unescape(text)
    # Clean excessive whitespace while preserving newlines
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    clean_text = "\n".join(lines)
    clean_text = re.sub(r"\n{3,}", "\n\n", clean_text).strip()
    return clean_text


def _normalize_search_query(query: str, target_library: str | None = None) -> tuple[str, str]:
    """Clean and optimize a query and target library for dynamic web/doc search.

    Removes local file paths, extracts the primary error line from tracebacks,
    sanitizes target library version tags (e.g. 'celery_v4_to_v5' -> 'celery migration v4 to v5'),
    and forms a clean, context-rich search query across arbitrary library versions.
    """
    clean_query = query.strip()

    # 1. Clean target library and extract version migration context
    clean_lib = ""
    migration_hint = ""
    if target_library:
        raw_lib = target_library.strip()
        # Check patterns like "name-v1-to-v2" or "name_v4_to_v5"
        m_trans = re.search(r"([a-zA-Z0-9]+)[_\-](v?\d+)[_\-]to[_\-](v?\d+)", raw_lib, re.IGNORECASE)
        if m_trans:
            clean_lib = m_trans.group(1)
            migration_hint = f"{m_trans.group(2)} to {m_trans.group(3)} migration"
        else:
            # Check version constraints like "celery>=5.0,<6.0" or "pydantic>=2.0"
            m_ver = re.match(r"([a-zA-Z0-9_\-]+)([><=~^].*)?", raw_lib)
            if m_ver:
                clean_lib = m_ver.group(1).replace("-", " ").replace("_", " ").strip()
                ver_spec = m_ver.group(2) or ""
                digits = re.findall(r"\d+(?:\.\d+)?", ver_spec)
                if digits:
                    migration_hint = f"v{digits[0]} migration"
            else:
                clean_lib = raw_lib

    # 2. Extract error if multiline or traceback
    lines = clean_query.splitlines()
    if len(lines) > 1 or "Traceback" in clean_query or "File " in clean_query:
        error_lines = [
            line.strip().lstrip("E ").strip()
            for line in lines
            if line.strip().startswith("E ")
            or "Error" in line
            or "Exception" in line
            or "Warning" in line
        ]
        if error_lines:
            clean_query = error_lines[-1]
        else:
            for line in reversed(lines):
                if line.strip() and not line.strip().startswith("="):
                    clean_query = line.strip()
                    break

    # 3. Strip local file paths and line number noise
    clean_query = re.sub(r"\([A-Za-z]:\\[^)]+\)", "", clean_query)
    clean_query = re.sub(r"\(/[^)]+\)", "", clean_query)
    clean_query = re.sub(r"[A-Za-z]:\\[^\"'\s]+", "", clean_query)
    clean_query = re.sub(r"/(?:usr|home|app|tmp|workspace|lib|site-packages)/[^\"'\s]+", "", clean_query)
    clean_query = re.sub(r",?\s*line\s+\d+.*", "", clean_query)
    clean_query = re.sub(r"\s+", " ", clean_query).strip()

    # 4. Assemble composite query with library and version context
    parts = []
    if clean_lib and clean_lib.lower() not in clean_query.lower():
        parts.append(clean_lib)
    if migration_hint and migration_hint.lower() not in clean_query.lower():
        parts.append(migration_hint)
    parts.append(clean_query)

    final_search_query = " ".join(p for p in parts if p).strip()
    return final_search_query, clean_lib


@dataclass
class CorpusChunk:
    doc_ref: str
    target_library: str
    file_rel_path: str
    title: str
    anchor: str
    content: str
    tokens: list[str]
    length: int


class BM25Index:
    """Okapi BM25 implementation for ranking markdown documentation chunks."""

    def __init__(self, chunks: list[CorpusChunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.doc_count = len(chunks)
        self.avg_doc_len = (
            sum(c.length for c in chunks) / self.doc_count if self.doc_count > 0 else 0.0
        )

        # Term document frequency: how many chunks contain term t
        self.doc_freq: dict[str, int] = Counter()
        self.term_freqs: list[dict[str, int]] = []

        for chunk in chunks:
            tf = Counter(chunk.tokens)
            self.term_freqs.append(tf)
            for term in tf:
                self.doc_freq[term] += 1

    def idf(self, term: str) -> float:
        """Calculate Okapi BM25 IDF for a term."""
        df = self.doc_freq.get(term, 0)
        return math.log(((self.doc_count - df + 0.5) / (df + 0.5)) + 1.0)

    def score(self, query_tokens: list[str]) -> list[tuple[CorpusChunk, float]]:
        """Score all chunks against query tokens, returning (chunk, score) sorted desc."""
        if not self.chunks or not query_tokens:
            return []

        scores: list[tuple[CorpusChunk, float]] = []
        for idx, chunk in enumerate(self.chunks):
            doc_len = chunk.length
            tf_dict = self.term_freqs[idx]
            score = 0.0

            for term in query_tokens:
                if term not in tf_dict:
                    continue
                tf = tf_dict[term]
                term_idf = self.idf(term)
                denom = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / self.avg_doc_len))
                score += term_idf * (tf * (self.k1 + 1.0) / denom)

            if score > 0.0:
                scores.append((chunk, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores


def _parse_markdown_sections(
    markdown_text: str, file_rel_path: str, target_library: str
) -> list[CorpusChunk]:
    """Parse a markdown document into structured sections/chunks based on primary headings."""
    lines = markdown_text.splitlines()
    chunks: list[CorpusChunk] = []

    doc_title = Path(file_rel_path).stem.replace("-", " ").title()

    # Determine primary chunk heading level (prefer level 2 if present, else level 1, else level 3)
    has_h2 = any(re.match(r"^##\s+", line) for line in lines)
    chunk_level = 2 if has_h2 else 1

    heading_re = re.compile(rf"^(#{{{chunk_level}}})\s+(.*)$")

    current_title = doc_title
    current_anchor = ""
    current_lines: list[str] = []

    def flush_chunk() -> None:
        nonlocal current_lines, current_title, current_anchor
        if not current_lines:
            return
        content = "\n".join(current_lines).strip()
        if content:
            anchor_slug = current_anchor or _slugify(current_title)
            doc_ref = f"{file_rel_path}#{anchor_slug}" if anchor_slug else file_rel_path
            tokens = _extract_tokens(f"{doc_title} {current_title} {content}")
            chunks.append(
                CorpusChunk(
                    doc_ref=doc_ref,
                    target_library=target_library,
                    file_rel_path=file_rel_path,
                    title=current_title,
                    anchor=anchor_slug,
                    content=content,
                    tokens=tokens,
                    length=len(tokens),
                )
            )
        current_lines = []

    for line in lines:
        match = heading_re.match(line)
        if match:
            flush_chunk()
            heading_text = match.group(2).strip()
            current_title = heading_text
            current_anchor = _slugify(heading_text)
            current_lines = [line]
        else:
            current_lines.append(line)

    flush_chunk()
    return chunks


def _extract_exact_section(markdown_text: str, anchor: str) -> tuple[str, str] | None:
    """Extract a specific section from markdown matching an anchor slug or title.

    Returns:
        (title, section_content) or None if not found.
    """
    lines = markdown_text.splitlines()
    heading_re = re.compile(r"^(#{1,6})\s+(.*)$")
    target_slug = _slugify(anchor)

    matched_level: int | None = None
    matched_title: str | None = None
    section_lines: list[str] = []
    in_section = False

    for line in lines:
        match = heading_re.match(line)
        if match:
            level = len(match.group(1))
            heading_text = match.group(2).strip()
            heading_slug = _slugify(heading_text)

            if in_section:
                # Stop if another heading of same or higher level is reached
                if level <= (matched_level or 1):
                    break
                else:
                    section_lines.append(line)
            else:
                if heading_slug == target_slug or _slugify(anchor) in heading_slug or anchor.lower() == heading_text.lower():
                    in_section = True
                    matched_level = level
                    matched_title = heading_text
                    section_lines.append(line)
        elif in_section:
            section_lines.append(line)

    if in_section and matched_title:
        return matched_title, "\n".join(section_lines).strip()

    return None


def _find_corpus_file(corpus_dir: Path, file_name: str) -> tuple[Path, str, str]:
    """Find a file within corpus_dir.

    Returns:
        (resolved_file_path, target_library, relative_file_path_str)
    """
    clean_ref = file_name.replace("\\", "/").strip("/")

    # Check direct relative path
    direct_path = (corpus_dir / clean_ref).resolve()
    if direct_path.exists() and direct_path.is_file():
        rel = direct_path.relative_to(corpus_dir).as_posix()
        target_lib = rel.split("/")[0] if "/" in rel else "general"
        return direct_path, target_lib, rel

    # Check recursive search by basename
    base_name = Path(clean_ref).name
    matches = list(corpus_dir.rglob(base_name))
    if matches:
        found = matches[0].resolve()
        rel = found.relative_to(corpus_dir).as_posix()
        target_lib = rel.split("/")[0] if "/" in rel else "general"
        return found, target_lib, rel

    raise FileNotFoundError(f"Corpus file '{file_name}' not found under '{corpus_dir}'")


def _index_all_corpus(corpus_dir: Path, target_library: str | None = None) -> list[CorpusChunk]:
    """Scan and index all markdown files in the corpus directory."""
    if not corpus_dir.exists() or not corpus_dir.is_dir():
        return []

    search_dir = corpus_dir
    clean_target = (
        re.sub(r"[_\-]?v?\d+.*$", "", target_library).strip()
        if target_library
        else ""
    )
    if target_library:
        lib_dir = corpus_dir / target_library
        if not (lib_dir.exists() and lib_dir.is_dir()) and clean_target:
            lib_dir = corpus_dir / clean_target
        if lib_dir.exists() and lib_dir.is_dir():
            search_dir = lib_dir

    all_chunks: list[CorpusChunk] = []
    for md_path in search_dir.rglob("*.md"):
        try:
            content = md_path.read_text(encoding="utf-8", errors="replace")
            rel_path = md_path.relative_to(corpus_dir).as_posix()
            lib_name = rel_path.split("/")[0] if "/" in rel_path else "general"
            if target_library and lib_name != target_library:
                if not (clean_target and (lib_name == clean_target or clean_target in lib_name or lib_name in target_library)):
                    continue
            chunks = _parse_markdown_sections(content, rel_path, lib_name)
            all_chunks.extend(chunks)
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Failed to parse markdown corpus file", path=str(md_path), error=str(exc))

    return all_chunks


def create_docs_server(corpus_dir: str | Path | None = None) -> MCPServer:
    """Create and configure the mcp-server-docs MCP server."""
    server = MCPServer("mcp-server-docs")

    resolved_corpus_dir = (
        Path(corpus_dir).resolve()
        if corpus_dir
        else Path(load_config().docs_corpus_dir).resolve()
    )

    @server.tool()
    def lookup_doc_ref(doc_ref: str) -> dict[str, Any]:
        """Retrieve exact documentation content from the Doc Corpus via a doc_ref.

        Args:
            doc_ref: Documentation reference string (e.g.,
                     'pydantic-v2-migration.md#validator-to-field-validator' or
                     'pydantic/pydantic-v2-migration.md#validator-to-field-validator').

        Returns:
            Dict with doc_ref, file_path, section, title, and content.
        """
        if not doc_ref or not doc_ref.strip():
            raise ValueError("doc_ref cannot be empty")

        clean_ref = doc_ref.strip()
        file_part, _, anchor = clean_ref.partition("#")
        file_part = file_part.strip()
        anchor = anchor.strip()

        file_path, target_lib, rel_path = _find_corpus_file(resolved_corpus_dir, file_part)
        file_content = file_path.read_text(encoding="utf-8")

        if not anchor:
            # Return entire document
            title = Path(file_part).stem.replace("-", " ").title()
            return {
                "doc_ref": doc_ref,
                "target_library": target_lib,
                "file_path": rel_path,
                "section": None,
                "title": title,
                "content": file_content,
            }

        section_result = _extract_exact_section(file_content, anchor)
        if not section_result:
            raise ValueError(f"Section '{anchor}' not found in '{file_part}'")

        title, content = section_result
        return {
            "doc_ref": doc_ref,
            "target_library": target_lib,
            "file_path": rel_path,
            "section": anchor,
            "title": title,
            "content": content,
        }

    @server.tool()
    def search_corpus(
        query: str,
        target_library: str | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Perform BM25 keyword search over all Doc Corpus markdown files.

        Args:
            query: Search query or error snippet.
            target_library: Optional target library filter (e.g. 'pydantic').
            top_k: Maximum number of relevant chunks to return (default: 5).

        Returns:
            Dict containing query, target_library, total_results, and list of ranked chunks.
        """
        if not query or not query.strip():
            raise ValueError("query cannot be empty")

        chunks = _index_all_corpus(resolved_corpus_dir, target_library=target_library)
        if not chunks:
            return {
                "query": query,
                "target_library": target_library,
                "total_results": 0,
                "results": [],
            }

        index = BM25Index(chunks)
        query_tokens = _extract_tokens(query)
        scored_chunks = index.score(query_tokens)

        top_results = scored_chunks[:top_k]
        results_data = [
            {
                "doc_ref": chunk.doc_ref,
                "target_library": chunk.target_library,
                "file_path": chunk.file_rel_path,
                "title": chunk.title,
                "score": round(score, 4),
                "content": chunk.content,
            }
            for chunk, score in top_results
        ]

        return {
            "query": query,
            "target_library": target_library,
            "total_results": len(results_data),
            "results": results_data,
        }

    @server.tool()
    def web_search(
        query: str,
        target_library: str | None = None,
        max_tokens: int = 1500,
    ) -> dict[str, Any]:
        """Perform a constrained web search fallback via Tavily API or DuckDuckGo.

        Returns top-3 relevant chunks with a strict total budget of <= 1500 tokens,
        formatted as clean markdown without HTML artifacts.

        Args:
            query: The error traceback or search query.
            target_library: Optional target library context.
            max_tokens: Maximum total tokens across all returned chunks (<= 1500).

        Returns:
            Dict containing query, source, total_tokens, and chunks.
        """
        if not query or not query.strip():
            raise ValueError("query cannot be empty")

        max_tokens = min(max_tokens, 1500)
        config = load_config()
        tavily_key = getattr(config, "tavily_api_key", None)
        if tavily_key is None and not hasattr(config, "tavily_api_key"):
            tavily_key = os.environ.get("TAVILY_API_KEY")

        search_query, clean_lib = _normalize_search_query(query, target_library)

        raw_chunks: list[dict[str, str]] = []
        source_name = "tavily"
        tavily_answer: str | None = None

        if tavily_key:
            try:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(
                        "https://api.tavily.com/search",
                        json={
                            "api_key": tavily_key,
                            "query": search_query,
                            "search_depth": "basic",
                            "max_results": 5,
                            "include_raw_content": False,
                            "include_answer": True,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    tavily_answer = data.get("answer")
                    for item in data.get("results", []):
                        raw_chunks.append({
                            "title": item.get("title", ""),
                            "url": item.get("url", ""),
                            "content": item.get("content", ""),
                        })
            except (httpx.HTTPError, OSError) as exc:
                logger.warning("Tavily search failed, falling back to DuckDuckGo", error=str(exc))
                raw_chunks = []
                tavily_answer = None

        if not raw_chunks:
            source_name = "duckduckgo"
            try:
                # Query DuckDuckGo HTML endpoint
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                }
                with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
                    resp = client.post(
                        "https://html.duckduckgo.com/html/",
                        data={"q": search_query},
                    )
                    resp.raise_for_status()
                    html_content = resp.text

                    # Parse results from HTML using regex (matching DuckDuckGo's result__a links and snippets)
                    result_blocks = re.findall(
                        r'<div[^>]*class="[^"]*result__body[^"]*"[^>]*>.*?<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
                        html_content,
                        re.DOTALL,
                    )
                    if not result_blocks:
                        # Alternative result block pattern (e.g. h2 wrapping result__a or result__url)
                        result_blocks = re.findall(
                            r'<h2[^>]*>.*?<a[^>]*class="[^"]*result__[a-z]+"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?</h2>.*?<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
                            html_content,
                            re.DOTALL,
                        )
                    if not result_blocks:
                        # Fallback: extract result__a links and result__snippet tags directly
                        urls_titles = re.findall(
                            r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                            html_content,
                            re.DOTALL,
                        )
                        snippets = re.findall(
                            r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
                            html_content,
                            re.DOTALL,
                        )
                        result_blocks = [
                            (u, t, s)
                            for (u, t), s in zip(urls_titles, snippets)
                        ]

                    for raw_url, raw_title, raw_snippet in result_blocks[:5]:
                        clean_url = raw_url
                        if "uddg=" in raw_url:
                            # Extract actual destination from duckduckgo redirect url
                            match = re.search(r"uddg=([^&]+)", raw_url)
                            if match:
                                clean_url = unquote(match.group(1))

                        raw_chunks.append({
                            "title": _clean_html_artifacts(raw_title),
                            "url": clean_url,
                            "content": _clean_html_artifacts(raw_snippet),
                        })
            except (httpx.HTTPError, OSError) as exc:
                logger.warning("DuckDuckGo search failed", error=str(exc))

        # Enforce budget: top-3 chunks, total tokens <= max_tokens, clean markdown
        final_chunks: list[dict[str, Any]] = []
        accumulated_tokens = 0

        # Prepend synthesized answer from Tavily if available
        if isinstance(tavily_answer, str) and tavily_answer.strip():
            clean_ans = _clean_html_artifacts(tavily_answer)
            ans_tokens = _count_tokens(clean_ans)
            if ans_tokens <= max_tokens:
                heading_lib = clean_lib.capitalize() if clean_lib else "Migration"
                final_chunks.append({
                    "title": f"Tavily Migration Synthesis: {heading_lib}",
                    "url": "https://api.tavily.com",
                    "content": clean_ans,
                    "tokens": ans_tokens,
                })
                accumulated_tokens += ans_tokens

        for item in raw_chunks:
            if len(final_chunks) >= 3:
                break

            clean_text = _clean_html_artifacts(item.get("content", ""))
            clean_title = _clean_html_artifacts(item.get("title", ""))
            if not clean_text:
                continue

            chunk_tokens = _count_tokens(clean_text)
            remaining_budget = max_tokens - accumulated_tokens

            if remaining_budget <= 0:
                break

            if chunk_tokens > remaining_budget:
                # Truncate content to fit within remaining budget
                # Approximate character budget
                approx_char_budget = remaining_budget * 4
                truncated_text = clean_text[:approx_char_budget].rsplit(" ", 1)[0] + "..."
                chunk_tokens = _count_tokens(truncated_text)
                clean_text = truncated_text

            final_chunks.append({
                "title": clean_title,
                "url": item.get("url", ""),
                "content": clean_text,
                "tokens": chunk_tokens,
            })
            accumulated_tokens += chunk_tokens

        return {
            "query": query,
            "search_query": search_query,
            "target_library": target_library,
            "source": source_name,
            "answer": tavily_answer,
            "total_tokens": accumulated_tokens,
            "chunks": final_chunks,
        }

    return server


def main() -> None:
    """Run the mcp-server-docs server with stdio transport."""
    server = create_docs_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
