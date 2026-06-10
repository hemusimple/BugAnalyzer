"""
repo_manager.py — Clone/pull GitHub and GitLab repos, then index them.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger

from config import settings
from agent.code_indexer import registry, CodeIndex


def _inject_token(url: str) -> str:
    """Inject PAT into clone URL."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    token = ""
    if "github" in host:
        token = settings.GITHUB_TOKEN
    elif "gitlab" in host:
        token = settings.GITLAB_TOKEN

    if token:
        return url.replace("https://", f"https://oauth2:{token}@")
    return url


def _repo_name_from_url(url: str) -> str:
    """e.g. https://github.com/org/repo → org/repo"""
    url = url.rstrip("/").removesuffix(".git")
    parts = url.split("/")
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1]


def _repo_dir(repo_name: str) -> Path:
    safe = repo_name.replace("/", "__")
    return settings.REPOS_DIR / safe


async def clone_or_pull(url: str) -> tuple[str, Path]:
    """Clone repo if not present, else git pull. Returns (repo_name, local_path)."""
    repo_name = _repo_name_from_url(url)
    dest = _repo_dir(repo_name)

    auth_url = _inject_token(url)

    if dest.exists():
        logger.info(f"Pulling {repo_name}...")
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(dest), "pull", "--ff-only",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(f"git pull failed for {repo_name}: {stderr.decode()}")
    else:
        logger.info(f"Cloning {repo_name}...")
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", auth_url, str(dest),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git clone failed: {stderr.decode()}")

    return repo_name, dest


async def index_repo(repo_name: str, repo_dir: Path):
    """Build BM25 index for a repo (runs in thread pool to not block event loop)."""
    loop = asyncio.get_event_loop()

    def _build():
        idx = CodeIndex(settings.INDEX_DIR)
        idx.build(repo_dir, repo_name)
        registry._indexes[repo_name] = idx

    await loop.run_in_executor(None, _build)


async def add_repo(url: str) -> dict:
    """Full pipeline: clone + index. Returns status dict."""
    try:
        repo_name, repo_dir = await clone_or_pull(url)
        await index_repo(repo_name, repo_dir)
        idx = registry.get_or_create(repo_name)
        return {
            "status": "ok",
            "repo": repo_name,
            "chunks": len(idx.chunks),
            "log_tags": len(idx.log_tag_map),
        }
    except Exception as e:
        logger.exception(f"Failed to add repo {url}: {e}")
        return {"status": "error", "repo": url, "error": str(e)}


async def load_all_existing():
    """On startup: load all existing indexes from disk without re-cloning."""
    if not settings.INDEX_DIR.exists():
        return
    for f in settings.INDEX_DIR.glob("*.json"):
        # Reconstruct repo_name from filename
        repo_name = f.stem.replace("__", "/")
        try:
            idx = CodeIndex(settings.INDEX_DIR)
            if idx.load(repo_name):
                registry._indexes[repo_name] = idx
                logger.info(f"Restored index: {repo_name}")
        except Exception as e:
            logger.warning(f"Could not restore {repo_name}: {e}")


async def startup_index_repos():
    """Clone and index all repos from .env REPO_URLS."""
    await load_all_existing()
    for url in settings.REPO_URLS:
        if url:
            asyncio.create_task(add_repo(url))
