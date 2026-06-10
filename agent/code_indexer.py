"""
code_indexer.py — Index codebases for fast context retrieval.
Uses BM25 for keyword search + stores log-tag → file mappings.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from loguru import logger
from rank_bm25 import BM25Okapi

from config import settings

# Extensions to index
CODE_EXTENSIONS = {
    ".kt", ".java", ".xml", ".gradle", ".kts", ".py",
    ".cpp", ".h", ".c", ".swift", ".dart", ".go",
}

SKIP_DIRS = {
    ".git", "build", ".gradle", "__pycache__", "node_modules",
    ".idea", "generated", "bin", "obj", ".dart_tool",
}


def _tokenize(text: str) -> list[str]:
    """Simple tokenizer: lowercase, split on non-word chars."""
    return re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{1,}", text.lower())


class CodeIndex:
    """
    Indexes a directory of source files. Provides:
    - BM25 full-text search over chunks
    - Log tag → file mapping (Android TAG = "ClassName" patterns)
    - Layer/component detection
    """

    def __init__(self, index_path: Path):
        self.index_path = index_path
        self.chunks: list[dict] = []        # {file, start_line, end_line, text, tokens}
        self.log_tag_map: dict[str, list[str]] = {}   # TAG -> [file_path]
        self.bm25: Optional[BM25Okapi] = None
        self.repo_name: str = ""
        self._loaded = False

    # ------------------------------------------------------------------ build
    def build(self, repo_dir: Path, repo_name: str):
        self.repo_name = repo_name
        self.chunks = []
        self.log_tag_map = {}

        logger.info(f"Indexing repo: {repo_name} @ {repo_dir}")
        chunk_size = settings.CHUNK_SIZE

        for fpath in self._walk(repo_dir):
            rel = str(fpath.relative_to(repo_dir))
            try:
                lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue

            # Extract log tags for Android projects
            self._extract_log_tags(lines, rel)

            # Chunk file
            for start in range(0, len(lines), chunk_size // 2):  # 50% overlap
                end = min(start + chunk_size, len(lines))
                text = "\n".join(lines[start:end])
                tokens = _tokenize(text)
                if not tokens:
                    continue
                self.chunks.append({
                    "file": rel,
                    "start_line": start + 1,
                    "end_line": end,
                    "text": text,
                    "tokens": tokens,
                    "repo": repo_name,
                })

        if self.chunks:
            self.bm25 = BM25Okapi([c["tokens"] for c in self.chunks])

        self._loaded = True
        self._save()
        logger.info(f"Indexed {len(self.chunks)} chunks, {len(self.log_tag_map)} log tags for {repo_name}")

    def _extract_log_tags(self, lines: list[str], rel_path: str):
        """Extract Android Log TAG declarations and usages."""
        tag_patterns = [
            r'(?:private\s+)?(?:static\s+)?(?:final\s+)?(?:val|var|String)\s+TAG\s*=\s*["\']([^"\']+)["\']',
            r'Log\.[diwev]\(\s*["\']([^"\']+)["\']',
            r'Timber\.\w+\([^)]*\)',  # Timber (no tag but class name)
            r'class\s+(\w+)',         # class name as fallback tag
        ]
        for line in lines:
            for pat in tag_patterns[:2]:  # primary TAG extraction
                m = re.search(pat, line)
                if m:
                    tag = m.group(1)
                    if tag not in self.log_tag_map:
                        self.log_tag_map[tag] = []
                    if rel_path not in self.log_tag_map[tag]:
                        self.log_tag_map[tag].append(rel_path)

    def _walk(self, root: Path):
        for p in root.rglob("*"):
            if p.is_file() and p.suffix in CODE_EXTENSIONS:
                if not any(skip in p.parts for skip in SKIP_DIRS):
                    yield p

    # ----------------------------------------------------------------- search
    def search(self, query: str, top_k: int = 8) -> list[dict]:
        if not self._loaded or self.bm25 is None:
            return []
        tokens = _tokenize(query)
        scores = self.bm25.get_scores(tokens)
        top_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        results = []
        seen_files = set()
        for idx in top_idx:
            if scores[idx] < 0.01:
                continue
            chunk = self.chunks[idx]
            key = f"{chunk['file']}:{chunk['start_line']}"
            if key not in seen_files:
                seen_files.add(key)
                results.append({**chunk, "score": float(scores[idx])})
        return results

    def lookup_tag(self, tag: str) -> list[str]:
        """Return files that define or use this log tag."""
        if tag in self.log_tag_map:
            return self.log_tag_map[tag]
        # fuzzy: partial match — flatten file lists from all matching tags
        result = []
        for t, files in self.log_tag_map.items():
            if tag.lower() in t.lower() or t.lower() in tag.lower():
                result.extend(files)
        return result

    # ------------------------------------------------------------------- I/O
    def _save(self):
        self.index_path.mkdir(parents=True, exist_ok=True)
        safe = self.repo_name.replace("/", "_").replace(":", "_")
        idx_file = self.index_path / f"{safe}.json"
        data = {
            "repo": self.repo_name,
            "chunks": self.chunks,
            "log_tag_map": self.log_tag_map,
        }
        with open(idx_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.info(f"Saved index: {idx_file}")

    def load(self, repo_name: str) -> bool:
        safe = repo_name.replace("/", "_").replace(":", "_")
        idx_file = self.index_path / f"{safe}.json"
        if not idx_file.exists():
            return False
        with open(idx_file, encoding="utf-8") as f:
            data = json.load(f)
        self.repo_name = data["repo"]
        self.chunks = data["chunks"]
        self.log_tag_map = data["log_tag_map"]
        if self.chunks:
            self.bm25 = BM25Okapi([c["tokens"] for c in self.chunks])
        self._loaded = True
        logger.info(f"Loaded index: {repo_name} ({len(self.chunks)} chunks)")
        return True


# ------------------------------------------------------------------ registry

class IndexRegistry:
    """Holds multiple CodeIndex objects — one per repo."""

    def __init__(self):
        self._indexes: dict[str, CodeIndex] = {}

    def get_or_create(self, repo_name: str) -> CodeIndex:
        if repo_name not in self._indexes:
            idx = CodeIndex(settings.INDEX_DIR)
            if not idx.load(repo_name):
                pass  # will be built by repo_manager
            self._indexes[repo_name] = idx
        return self._indexes[repo_name]

    def all_indexes(self) -> list[CodeIndex]:
        return list(self._indexes.values())

    def search_all(self, query: str, top_k: int = 8) -> list[dict]:
        results = []
        per_repo = max(2, top_k // max(len(self._indexes), 1))
        for idx in self._indexes.values():
            results.extend(idx.search(query, top_k=per_repo))
        # Sort by score globally
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def lookup_tag_all(self, tag: str) -> list[str]:
        files = []
        for idx in self._indexes.values():
            files.extend(idx.lookup_tag(tag))
        return list(set(files))

    def stats(self) -> dict:
        return {
            repo: {
                "chunks": len(idx.chunks),
                "log_tags": len(idx.log_tag_map),
            }
            for repo, idx in self._indexes.items()
        }


registry = IndexRegistry()
