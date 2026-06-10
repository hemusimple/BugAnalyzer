# Android Log Analyzer — Agentic AI

A production-grade agentic log analysis system for Android projects. Integrates with Jira webhooks, local LLMs (Ollama/Mistral), and multi-repo codebases to automatically diagnose bugs.

## Architecture

```
log-analyzer/
├── agent/                  # Core agent logic
│   ├── __init__.py
│   ├── orchestrator.py     # Main agent loop (ReAct)
│   ├── tools.py            # Agent tools
│   ├── jira_client.py      # Jira webhook + attachment downloader
│   ├── repo_manager.py     # GitHub/GitLab cloner + indexer
│   ├── log_parser.py       # Log file parser + pattern extractor
│   ├── code_indexer.py     # TF-IDF + BM25 code search
│   ├── llm_client.py       # Ollama/Mistral client with token tracking
│   └── architecture_analyzer.py  # App layer/arch understanding
├── api/
│   ├── __init__.py
│   ├── main.py             # FastAPI app
│   ├── websocket.py        # WebSocket for real-time streaming
│   └── models.py           # Pydantic models
├── ui/
│   └── index.html          # Single-file Web UI (split panel + chat)
├── data/
│   ├── repos/              # Cloned repos
│   ├── logs/               # Downloaded log files
│   └── indexes/            # BM25/TF-IDF indexes (JSON)
├── .env.example
├── requirements.txt
└── start.sh / start.bat
```

## Quick Start

1. Copy `.env.example` → `.env` and fill credentials
2. Install: `pip install -r requirements.txt`
3. Start Ollama: `ollama pull mistral`
4. Run: `python -m api.main`
5. Open: `http://localhost:8000`

## Features

- **Jira Webhook**: POST `/webhook/jira` — auto-downloads attachments on issue create/update
- **Multi-repo indexing**: Add GitHub/GitLab URLs via UI or `.env`
- **Agentic diagnosis**: ReAct loop with tools: search_code, read_file, search_logs, blame_layer
- **Architecture awareness**: Understands Service→Repository→Observer patterns
- **Token tracking**: Full LLM token usage tracked per session
- **Split UI**: LLM raw reasoning | Agent structured verdict | Chat follow-up
