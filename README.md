# Android Log Analyzer — Agentic AI

A self-hosted, agentic log analysis system for Android projects. It ingests logcat/bugreport files (uploaded directly or pulled from Jira attachments), reasons over them with a local LLM in a ReAct loop, cross-references your app's own source code, and produces a structured verdict: **is this an app bug or an external/system bug, which architectural layer is at fault, and which team should own it.**

## How it works

1. A log file arrives (manual upload, WebSocket upload, or auto-downloaded from a Jira issue attachment).
2. `log_parser.py` parses logcat lines (both `date time pid tid LEVEL TAG: msg` and `LEVEL/TAG(pid): msg` formats), extracting errors, exceptions, timeouts and the set of log tags seen.
3. `orchestrator.py` runs a **ReAct loop** (Think → Act → Observe, up to 8 steps) against the local LLM. The system prompt gives the model a fixed toolset and asks it to reason step by step using `<think>`, `<action>`, and `<final_answer>` XML tags.
4. On each step the model can call a tool (see below) to pull in log context or source code; results are fed back as observations for the next step. The last 2 steps force a final answer to bound total LLM calls, and conversation history is capped to avoid context-window blowup.
5. The final answer is parsed into a structured **Verdict** (`APP BUG` / `EXTERNAL BUG` / `INCONCLUSIVE`), **Blame Layer** (UI, ViewModel, Repository, Service, Android Framework, HAL/BSP, Backend/Server, ...) and **Recommended Team**.
6. If the analysis originated from a Jira webhook, the verdict is posted back as a comment on the issue.

### Tools available to the agent

| Tool | Purpose |
|---|---|
| `get_log_summary` | Error/exception/timeout counts, tags seen, per-layer tag distribution |
| `get_errors_and_exceptions` | Raw error/exception/timeout lines from the loaded log |
| `search_logs` | Keyword search over the log with surrounding context lines |
| `lookup_log_tag` | Find source files that declare/use a given Android `Log` tag |
| `search_code` | BM25 full-text search across all indexed repos |
| `read_file` | Read a specific file out of an indexed repo |
| `get_architecture_map` | Layer/component map inferred from the indexed codebase |
| `blame_analysis` | Map a set of log tags to the components/layers responsible |
| `index_stats` | Chunk/tag counts per indexed repo |
| `note` | Scratch space for the agent to record intermediate reasoning |

### Codebase indexing & architecture inference

- `repo_manager.py` clones (shallow, `--depth=1`) or pulls GitHub/GitLab repos listed in `REPO_URLS` or added via the UI, injecting a PAT if configured.
- `code_indexer.py` walks each repo (skipping `build/`, `.git/`, `node_modules/`, etc.), chunks source files (Kotlin/Java/XML/Gradle/Python/C++/Swift/Dart/Go) with 50% overlap, and builds a **BM25** index (`rank-bm25`) per repo, persisted to `data/indexes/*.json`. It also regex-extracts `Log` `TAG` declarations to build a tag → file map.
- `architecture_analyzer.py` classifies each discovered class into a layer (UI/Compose, ViewModel, UseCase, Repository, DataSource, Service/Manager, HAL/VHAL, Model/Entity) using filename and class-name heuristics, and infers data-flow edges (UI → ViewModel → Repository → Service) from constructor-style dependency declarations.

## Architecture

```
log-analyzer/
├── agent/
│   ├── orchestrator.py         # ReAct loop: think/act/observe → structured verdict
│   ├── tools.py                # Tool registry + implementations used by the agent
│   ├── llm_client.py           # Ollama chat client (streaming) + token/latency tracking
│   ├── log_parser.py           # Logcat parsing, error/exception/timeout extraction
│   ├── code_indexer.py         # Per-repo BM25 index + log-tag → file map
│   ├── repo_manager.py         # Clone/pull GitHub/GitLab repos, trigger indexing
│   ├── architecture_analyzer.py# Infers layers/components/data-flows from indexed code
│   └── jira_client.py          # Jira REST client, webhook signature check, attachment DL
├── api/
│   └── main.py                 # FastAPI app: REST endpoints, WebSocket, Jira webhook
├── ui/
│   └── index.html               # Single-file web UI (upload/chat/streaming verdict view)
├── config.py                    # Settings loaded from .env
├── data/                        # Created at runtime (gitignored)
│   ├── repos/                   # Cloned repos, one dir per `org__repo`
│   ├── logs/                    # Uploaded/downloaded log files
│   └── indexes/                 # Persisted BM25 indexes (JSON)
├── .env.example                 # Copy to .env and fill in
├── requirements.txt
└── start.sh / start.bat         # Setup + launch helper scripts
```

## Prerequisites

- Python 3.9+
- [Ollama](https://ollama.com) installed and running locally
- Git (for repo cloning/indexing)
- A Jira Cloud site + API token, only if you want webhook-driven auto-analysis

---

## Setup — Linux / macOS

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
# or use the helper script (also creates the venv, installs deps, and checks Ollama):
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

## Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server address |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Must be pulled beforehand (`ollama pull ...`) |
| `OLLAMA_TEMPERATURE` | `0.1` | Lower = more deterministic verdicts |
| `OLLAMA_CONTEXT_WINDOW` | `8192` | Passed as `num_ctx`; raise if your model/hardware supports it |
| `REPO_URLS` | *(empty)* | Comma-separated repo URLs to clone + index on startup |
| `GITHUB_TOKEN` / `GITLAB_TOKEN` | *(empty)* | PAT injected into clone URLs for private repos |
| `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` | *(empty)* | Required only for Jira webhook / attachment download / comment posting |
| `JIRA_WEBHOOK_SECRET` | *(empty)* | Leave **empty** — Jira Cloud does not sign webhook payloads with `X-Hub-Signature` by default |
| `INDEX_DIR`, `REPOS_DIR`, `LOGS_DIR` | `data/indexes`, `data/repos`, `data/logs` | Created automatically on startup |
| `HOST`, `PORT`, `LOG_LEVEL` | `0.0.0.0`, `8000`, `info` | Uvicorn server settings |

See `.env.example` for the full list.

---

## Usage

### Web UI
Open `http://localhost:8000` — upload a log file, optionally add a Jira-style summary/description, and watch the agent's reasoning stream in via WebSocket. A follow-up chat panel lets you ask questions about a completed analysis.

### REST API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/analyze` | Upload a log file (multipart) + optional summary/description, get back the full verdict |
| `POST` | `/api/repos/add` | `{"url": "https://github.com/org/repo"}` — clone + index a repo |
| `GET` | `/api/repos` | List indexed repos with chunk/tag counts |
| `POST` | `/api/repos/rebuild` | Rebuild the architecture map from currently indexed repos |
| `GET` | `/api/status` | LLM availability, model, indexed repo count, session token usage |
| `GET` | `/api/logs` / `DELETE /api/logs/{filename}` | List/remove previously uploaded logs |
| `POST` | `/api/token_usage/reset` | Reset the session's LLM token/latency counters |
| `WS` | `/ws/analyze` | Streaming analysis (`mode: "analyze"`) and follow-up chat (`mode: "chat"`) |

### Jira webhook (auto-analysis)

1. Set `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` in `.env`.
2. Expose the server publicly (e.g. `ngrok http 8000`) since Jira Cloud needs to reach it.
3. In Jira: **Settings → System → WebHooks**, point it at `https://<your-tunnel>/webhook/jira` for `issue created` / `issue updated` events.
4. On matching events, the app downloads any `.log`/`.txt`/bugreport-looking attachments, runs the ReAct analysis, and posts the verdict back as an issue comment.
5. Leave `JIRA_WEBHOOK_SECRET` empty — Jira Cloud webhooks aren't signed, so signature verification is skipped when this is unset.

---

## Features

- **Jira Webhook**: auto-downloads attachments and posts a verdict comment on issue create/update
- **Multi-repo indexing**: add GitHub/GitLab URLs via the UI or `REPO_URLS`, with private-repo PAT support
- **Agentic diagnosis**: bounded ReAct loop (max 8 steps + 1 forced synthesis) with 10 tools spanning log and code context
- **Architecture-aware blame**: infers UI/ViewModel/Repository/Service/HAL layers and data-flow edges from the indexed source, not just keyword matching
- **Token/latency tracking**: per-session LLM usage exposed via `/api/status` and each analysis result
- **Streaming UI**: live step-by-step reasoning over WebSocket, plus a follow-up chat mode
