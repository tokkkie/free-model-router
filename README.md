# Free Model Router

An OpenAI-compatible proxy server that automatically routes requests across OpenRouter's free models with failover support, falling back to a local Ollama model when all cloud models fail.

- Currently supports OpenRouter only.
- Ollama, Cerebras, Gemini, and other providers should work if they expose an OpenAI-compatible API — though providers without a model list API may require additional scraping.

## Features

- **OpenAI-compatible API** (`/v1/chat/completions`)
- **Dynamic model discovery** — Automatically fetches `:free` models from OpenRouter (actually checks pricing and selects models with cost = 0)
- **Priority routing** — Prefers capable models like `qwen`, `nemotron`, etc.
- **Auto failover** — Switches to the next model on 429 or timeout
- **Local fallback** — Falls back to Ollama when all cloud models fail
- **Streaming support** — Real-time responses via SSE
- **Tool call verification** — Automatically tests function calling support on newly detected models and excludes incompatible ones

## Directory Structure

```
free-model-router/
├── main.py                   # FastAPI server
├── config.json               # Timeout and priority settings
├── setup.sh                  # venv setup, dependency install, and server launch
├── requirements.txt          # Python dependencies
├── known_vendors.json        # Known vendor list (auto-updated)
│
├── adapters/
│   ├── __init__.py
│   ├── base.py               # Abstract adapter base
│   ├── openrouter.py         # OpenRouter adapter
│   └── ollama.py             # Ollama local adapter
│
├── router/
│   ├── __init__.py
│   ├── model_router.py            # Model list fetching and priority sorting
│   ├── failover.py                # 429/timeout detection and model switching
│   ├── tool_verifier.py           # Tests function calling support per model
│   └── tool_support_registry.py   # Caches tool support results
│
├── tests/                    # Test files
│
└── docs/
    └── how-it-works.md       # Architecture and flow diagrams
```

## Setup

### 1. Configure API Key

```bash
cp .env.example .env
# Edit .env and set your OPENROUTER_API_KEY
```

### 2. Start the server

```bash
./setup.sh
```

On first run, this creates a virtual environment and installs dependencies.
Subsequent runs start the server directly (default: `http://127.0.0.1:4141`).

### 3. Verify it's working

```bash
curl -X POST http://127.0.0.1:4141/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }'
```

## Configuration

All settings are in `config.json`:

| Key | Description |
|-----|-------------|
| `timeout_seconds` | Request timeout per model (seconds) |
| `model_cache_ttl_seconds` | How long to cache the model list (seconds) |
| `exclude_keywords` | Keywords to exclude models (e.g. models weak at your language) |
| `priority_keywords` | Keywords for model priority ordering |
| `ollama_model` | Local fallback model name |
| `verify_tool_support` | Test function calling support on startup (default: `true`) |
| `verify_timeout_seconds` | Timeout for verification requests (seconds) |
| `tool_support_cache_file` | File name for caching verification results |
| `rate_limit_cooldown_seconds` | Seconds to skip a model after a 429 (default: `60`, set `0` to disable) |

## Tool Call Verification

- On startup, only models **not yet recorded in the cache** are verified
- A simple function calling request is sent to check if `tool_calls` is returned
- Models that fail verification are automatically excluded from routing
- If verification fails due to 429 or timeout, the result is left pending and retried on next startup
- The cache file (`tool_support_cache.json`) is excluded from Git
 
