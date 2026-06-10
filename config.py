"""config.py — Central settings from .env"""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # Jira
    JIRA_BASE_URL: str = os.getenv("JIRA_BASE_URL", "")
    JIRA_EMAIL: str = os.getenv("JIRA_EMAIL", "")
    JIRA_API_TOKEN: str = os.getenv("JIRA_API_TOKEN", "")
    JIRA_WEBHOOK_SECRET: str = os.getenv("JIRA_WEBHOOK_SECRET", "")

    # Git
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    GITLAB_TOKEN: str = os.getenv("GITLAB_TOKEN", "")
    REPO_URLS: list[str] = [
        u.strip() for u in os.getenv("REPO_URLS", "").split(",") if u.strip()
    ]

    # LLM
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "mistral")
    OLLAMA_TEMPERATURE: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))
    OLLAMA_CONTEXT_WINDOW: int = int(os.getenv("OLLAMA_CONTEXT_WINDOW", "8192"))

    # Storage
    BASE_DIR: Path = Path(__file__).parent
    INDEX_DIR: Path = BASE_DIR / os.getenv("INDEX_DIR", "data/indexes")
    REPOS_DIR: Path = BASE_DIR / os.getenv("REPOS_DIR", "data/repos")
    LOGS_DIR: Path = BASE_DIR / os.getenv("LOGS_DIR", "data/logs")

    MAX_CONTEXT_FILES: int = int(os.getenv("MAX_CONTEXT_FILES", "10"))
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "120"))

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info")

    def __post_init__(self):
        self.INDEX_DIR.mkdir(parents=True, exist_ok=True)
        self.REPOS_DIR.mkdir(parents=True, exist_ok=True)
        self.LOGS_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
# Ensure dirs exist
settings.INDEX_DIR.mkdir(parents=True, exist_ok=True)
settings.REPOS_DIR.mkdir(parents=True, exist_ok=True)
settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
