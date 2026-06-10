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

## Prerequisites

- Python 3.9+
- [Ollama](https://ollama.com) installed and running
- Git

---

## Setup — Linux

```bash
# 1. Clone the repository
git clone https://github.com/hemusimple/BugAnalyzer.git
cd BugAnalyzer

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and fill in your credentials

# 5. Pull the LLM model
ollama pull qwen2.5-coder:7b

# 6. Start the server
python -m api.main
# or use the helper script:
bash start.sh
```

Open `http://localhost:8000` in your browser.

---

## Setup — Windows

```cmd
:: 1. Clone the repository
git clone https://github.com/hemusimple/BugAnalyzer.git
cd BugAnalyzer

:: 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate

:: 3. Install dependencies
pip install -r requirements.txt

:: 4. Configure environment
copy .env.example .env
:: Open .env in Notepad and fill in your credentials
notepad .env

:: 5. Pull the LLM model
ollama pull qwen2.5-coder:7b

:: 6. Start the server
python -m api.main
:: or use the helper script:
start.bat
```

Open `http://localhost:8000` in your browser.

---

## Features

- **Jira Webhook**: POST `/webhook/jira` — auto-downloads attachments on issue create/update
- **Multi-repo indexing**: Add GitHub/GitLab URLs via UI or `.env`
- **Agentic diagnosis**: ReAct loop with tools: search_code, read_file, search_logs, blame_layer
- **Architecture awareness**: Understands Service→Repository→Observer patterns
- **Token tracking**: Full LLM token usage tracked per session
- **Split UI**: LLM raw reasoning | Agent structured verdict | Chat follow-up
