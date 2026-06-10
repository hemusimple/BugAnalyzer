#!/bin/bash
# start.sh — Linux/macOS launcher

set -e

echo "═══════════════════════════════════"
echo "  Android Log Analyzer Agent"
echo "═══════════════════════════════════"

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found"
  exit 1
fi

# Create venv if needed
if [ ! -d ".venv" ]; then
  echo "→ Creating virtual environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate

# Install deps
echo "→ Installing dependencies..."
pip install -r requirements.txt -q

# Check .env
if [ ! -f ".env" ]; then
  echo "→ Creating .env from example..."
  cp .env.example .env
  echo "⚠️  Edit .env with your credentials before using Jira integration"
fi

# Check Ollama
if command -v ollama &>/dev/null; then
  echo "→ Ollama found"
  # Check if model is available
  MODEL=$(grep OLLAMA_MODEL .env 2>/dev/null | cut -d= -f2 || echo "mistral")
  echo "→ Model: ${MODEL}"
else
  echo "⚠️  Ollama not found. Install from https://ollama.ai"
  echo "   Then run: ollama pull mistral"
fi

# Create data dirs
mkdir -p data/repos data/logs data/indexes

echo ""
echo "→ Starting server..."
echo "→ Open http://localhost:8000"
echo ""

python3 -m api.main
