# Free Model Router

Free LLMs are great — until they aren't.

- Try a popular model → 429
- Try another → 429
- Try again → 429

Annoying. So let's just automate that.

Free Model Router cycles through multiple providers' free models automatically.
When one fails (429 or timeout), it moves on to the next without you doing anything.
No more manual model switching. No more babysitting rate limits.

If everything fails, it falls back to a local Ollama instance (basic implementation for now).

Run it locally and point your OpenAI-compatible client at it. That's it.

## Features

- **OpenAI-compatible API** (`/v1/chat/completions`)
- **Multi-provider support** — OpenRouter, Groq, Cerebras, SambaNova, and local Ollama
- **Dynamic model discovery** — Automatically fetches available models from each provider
- **Provider factory pattern** — Easy to add new providers (just 1 file + 1 line registration)
- **Priority routing** — Prefers capable models like `qwen`, `nemotron`, etc.
- **Auto failover** — Switches to the next model on 429, 404, or timeout
- **Ghost model detection** — Automatically excludes non-existent models (404) with 600s cooldown
- **Local fallback** — Falls back to Ollama when all cloud models fail
- **Streaming support** — Real-time responses via SSE
- **Tool call verification** — Automatically tests function calling support on newly detected models and excludes incompatible ones

## Directory Structure

```
free-model-router/
├── main.py                   # FastAPI server
├── config.yaml               # Provider and global settings
├── setup.sh                  # venv setup, dependency install, and server launch
├── requirements.txt          # Python dependencies
├── known_vendors.json        # Known vendor list (auto-updated)
│
├── adapters/
│   ├── __init__.py
│   ├── base.py               # Abstract adapter base
│   ├── provider_factory.py   # Provider factory for dynamic registration
│   ├── openrouter.py         # OpenRouter adapter
│   ├── groq.py               # Groq adapter
│   ├── cerebras.py           # Cerebras adapter
│   ├── sambanova.py          # SambaNova adapter
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

### 1. Configure API Keys

```bash
cp .env.example .env
# Edit .env and set your API keys:
# - OPENROUTER_API_KEY (required if OpenRouter is enabled)
# - GROQ_API_KEY (required if Groq is enabled)
# - CEREBRAS_API_KEY (required if Cerebras is enabled)
# - SAMBANOVA_API_KEY (required if SambaNova is enabled)
```

Edit `config.yaml` to enable/disable providers and configure settings.

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

All settings are in `config.yaml`:

| Key                           | Description                                                       |
| ----------------------------- | ----------------------------------------------------------------- |
| `timeout_seconds`             | Request timeout per model (seconds)                               |
| `model_cache_ttl_seconds`     | How long to cache the model list (seconds)                        |
| `exclude_keywords`            | Keywords to exclude models (e.g. models weak at your language)    |
| `priority_keywords`           | Keywords for model priority ordering                              |
| `ollama_model`                | Local fallback model name                                         |
| `verify_tool_support`         | Test function calling support on startup (default: `true`)        |
| `verify_timeout_seconds`      | Timeout for verification requests (seconds)                       |
| `tool_support_cache_file`     | File name for caching verification results                        |
| `rate_limit_cooldown_seconds` | Seconds to skip a model after a 429 (default: `600`, 10 minutes)  |
| `not_found_cooldown_seconds`  | Seconds to skip a model after a 404/422 (default: `3600`, 1 hour) |

## Tool Call Verification

- On startup, only models **not yet recorded in the cache** are verified
- A simple function calling request is sent to check if `tool_calls` is returned
- Models that fail verification are automatically excluded from routing
- If verification fails due to 429 or timeout, the result is left pending and retried on next startup
- The cache file (`tool_support_cache.json`) is excluded from Git
