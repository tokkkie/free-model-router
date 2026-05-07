# How It Works

## Overview

Free Model Router is a proxy server that automatically selects and fails over across multiple free AI models.

## Startup Behavior

### Model List Fetch Flow

```mermaid
sequenceDiagram
    participant App as FastAPI App
    participant MR as ModelRouter
    participant OR as OpenRouter API
    participant TSR as ToolSupportRegistry
    participant Cache as tool_support_cache.json

    App->>MR: get_free_models()
    MR->>OR: GET /models
    OR-->>MR: Full model list
    MR->>MR: Filter by :free + pricing==0
    MR->>MR: Exclude by exclude_keywords
    MR->>MR: Sort by priority_keywords
    MR-->>App: Prioritized free model list

    App->>TSR: prune(models)
    TSR->>Cache: Remove models no longer available

    App->>TSR: get_unverified(models)
    TSR-->>App: List of unverified models

    loop For each unverified model
        App->>OR: Tool support test
        OR-->>App: Result
        App->>TSR: mark(model, result)
    end
    TSR->>Cache: Save
```

### Step-by-Step

1. **Fetch models**: Retrieve the full model list from OpenRouter
2. **Filter free models**: Select models with `:free` suffix and pricing == 0
3. **Exclude**: Remove models matching `exclude_keywords` (e.g. dolphin, liquid, arcee)
4. **Sort**: Order by `priority_keywords` priority value (lower = higher priority)
5. **Verify tools**: Test function calling support on unverified models and cache results

---

## Request Handling

### Chat Completion Request

```mermaid
sequenceDiagram
    participant Client as Client
    participant App as FastAPI App
    participant MR as ModelRouter
    participant TSR as ToolSupportRegistry
    participant FR as FailoverRouter
    participant OR as OpenRouter
    participant Ollama as Ollama Local

    Client->>App: POST /v1/chat/completions

    App->>MR: get_free_models()
    MR->>MR: Check memory cache (TTL: 300s)
    MR-->>App: Model list

    App->>TSR: filter_supported(models)
    TSR-->>App: Tool-compatible models only

    loop Try models in priority order
        App->>FR: execute_with_failover()
        FR->>FR: Skip models in cooldown

        FR->>OR: chat_completion(model=1st)
        alt 200 OK
            OR-->>FR: Success response
            FR-->>App: Result
            App-->>Client: Response
        else 429 Rate Limit
            OR-->>FR: 429
            FR->>FR: Set COOLDOWN 120s
            FR->>FR: Try next model
        else Timeout
            FR->>FR: Try next model
        else 404 Not Found
            OR-->>FR: 404
            FR->>FR: Set COOLDOWN 600s
            FR->>FR: Try next model
        end
    end

    alt All models failed
        FR->>Ollama: chat_completion(local)
        Ollama-->>FR: Result
        FR-->>App: Result
        App-->>Client: Response
    end
```

### Streaming Request

```mermaid
sequenceDiagram
    participant Client as Client
    participant App as FastAPI App
    participant FR as FailoverRouter
    participant OR as OpenRouter

    Client->>App: POST /v1/chat/completions (stream=true)

    loop Each model
        FR->>OR: chat_completion_stream()
        alt Success
            OR-->>Client: Stream chunks
            OR-->>FR: Done
            FR-->>App: Exit
        else 429/Timeout
            FR->>FR: Failover to next model
        end
    end
```

---

## Cache Summary

| Cache             | Location                           | Content                     | TTL / Persistence             |
| ----------------- | ---------------------------------- | --------------------------- | ----------------------------- |
| **Model list**    | Memory (`_cached_models`)          | Free model list             | 300 seconds                   |
| **Tool support**  | `tool_support_cache.json`          | Per-model tool support flag | Persistent                    |
| **Cooldown**      | Class variable (`_cooldown_until`) | Rate-limited model state    | In-process (reset on restart) |
| **Ghost models**  | Memory cache                       | 404-detected models         | 600 seconds                   |
| **Known vendors** | `known_vendors.json`               | List of notified vendors    | Persistent                    |

---

## Failover Example

Actual log output and corresponding behavior:

```
2026-04-29 00:13:38,349 [WARNING] 429 Rate limit   qwen/qwen3-next-80b-a3b-instruct:free
2026-04-29 00:13:38,349 [INFO] COOLDOWN 120s   qwen/qwen3-next-80b-a3b-instruct:free
2026-04-29 00:13:50,310 [INFO] 200 OK (stream)   z-ai/glm-4.5-air:free
```

```mermaid
sequenceDiagram
    participant FR as FailoverRouter
    participant qwen as qwen/...
    participant glm as glm/...

    FR->>qwen: Request
    qwen-->>FR: 429 Rate Limit
    FR->>FR: Set COOLDOWN 120s
    FR->>glm: Request
    glm-->>FR: 200 OK
    FR-->>FR: Return response
```

---

## Configuration (`config.yaml`)

```yaml
global:
  timeout_seconds: 15
  model_cache_ttl_seconds: 300
  rate_limit_cooldown_seconds: 120
  not_found_cooldown_seconds: 600
  verify_tool_support: true
  cache_dir: .cache

enabled_providers:
  - openrouter
  - groq
  # - cerebras
  # - sambanova
  - ollama

providers:
  openrouter:
    base_url: https://openrouter.ai/api/v1
    priority_keywords:
      - keywords: [next, 80b, air]
        priority: 1
      - keywords: [nano, mini, lite, flash]
        priority: 98
    exclude_keywords: [dolphin, liquid, arcee]

  groq:
    base_url: https://api.groq.com/openai/v1
    min_context_window: 120000
    min_max_completion_tokens: 30000

  cerebras:
    base_url: https://api.cerebras.ai/v1

  sambanova:
    base_url: https://api.sambanova.ai/v1
    min_context_window: 128000
    min_max_completion_tokens: 8192

  ollama:
    base_url: http://localhost:11434
    model: phi3.5:latest
```

- **`enabled_providers`**: List of active providers (comment out to disable)
- **`exclude_keywords`**: Model name keywords to exclude from routing
- **`priority_keywords`**: Priority rules — lower value = higher priority
- **`rate_limit_cooldown_seconds`**: How long to skip a model after a 429
- **`not_found_cooldown_seconds`**: How long to skip a model after a 404

---

## Summary

1. **Startup**: Fetch free models, sort by priority, verify tool support
2. **Per request**: Try models in order, auto-skip on 429
3. **Fallback**: Route to local Ollama when all cloud models fail
4. **Cooldown**: Automatically skip rate-limited models for 120 seconds
5. **Ghost model handling**: 404 errors trigger 600s cooldown to exclude removed models
