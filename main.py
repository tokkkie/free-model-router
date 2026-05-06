import json
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from adapters.groq import GroqAdapter
from adapters.ollama import OllamaAdapter
from adapters.openrouter import OpenRouterAdapter
from router.failover import FailoverRouter
from router.model_router import ModelRouter
from router.tool_support_registry import ToolSupportRegistry
from router.tool_verifier import verify_tool_support

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

openrouter_api_key = os.getenv("OPENROUTER_API_KEY")

model_router = None
openrouter_adapter = None
if openrouter_api_key:
    model_router = ModelRouter(
        openrouter_base_url=config["openrouter_base_url"],
        priority_keywords=config["priority_keywords"],
        exclude_keywords=config.get("exclude_keywords"),
        cache_ttl=config["model_cache_ttl_seconds"],
    )
    openrouter_adapter = OpenRouterAdapter(
        api_key=openrouter_api_key,
        base_url=config["openrouter_base_url"],
    )
    logger.info("OpenRouter provider enabled")

ollama_adapter = OllamaAdapter(base_url=config["ollama_base_url"])

cloud_adapters = []
if openrouter_adapter:
    cloud_adapters.append(openrouter_adapter)

groq_api_key = os.getenv("GROQ_API_KEY")
groq_adapter = None
if groq_api_key and config.get("providers", {}).get("groq", {}).get("enabled", False):
    groq_base_url = config["providers"]["groq"].get("base_url", "https://api.groq.com/openai/v1")
    groq_adapter = GroqAdapter(api_key=groq_api_key, base_url=groq_base_url)
    cloud_adapters.append(groq_adapter)
    logger.info("Groq provider enabled")

# FailoverRouter は起動時に local_adapter を設定するため、グローバル変数として保持
failover_router = None

tool_support_registry = ToolSupportRegistry(
    cache_file=config.get("tool_support_cache_file", "tool_support_cache.json")
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global failover_router

    # Ollama の起動確認
    local_adapter = None
    local_model = config["ollama_model"]
    if await ollama_adapter.is_available():
        local_adapter = ollama_adapter
        logger.info(f"Ollama available at {config['ollama_base_url']}")
    else:
        logger.warning(f"Ollama not available at {config['ollama_base_url']}, skipping local fallback")

    # FailoverRouter を初期化
    failover_router = FailoverRouter(
        cloud_adapters=cloud_adapters,
        local_adapter=local_adapter,
        local_model=local_model,
        timeout=config["timeout_seconds"],
        cooldown_seconds=float(config.get("rate_limit_cooldown_seconds", 60)),
        not_found_cooldown_seconds=float(config.get("not_found_cooldown_seconds", 600)),
    )

    models = []
    if model_router is not None:
        logger.info("Fetching free models from OpenRouter...")
        models = await model_router.get_free_models()
        logger.info(f"Found {len(models)} free models")

    if groq_adapter is not None:
        await groq_adapter.list_models()

    pruned = tool_support_registry.prune(models) if models else 0
    if pruned:
        logger.info(f"Pruned {pruned} stale models from tool support cache")

    if config.get("verify_tool_support", True) and openrouter_adapter and models:
        unverified = tool_support_registry.get_unverified(models)
        if unverified:
            logger.info(
                f"{len(unverified)} new models detected, verifying tool support..."
            )
            verify_timeout = float(config.get("verify_timeout_seconds", 15))
            for m in unverified:
                result = await verify_tool_support(
                    openrouter_adapter, m, verify_timeout
                )
                if result is None:
                    logger.info(
                        f"  ? {m} (verification deferred, will retry next startup)"
                    )
                elif result:
                    tool_support_registry.mark(m, True)
                    logger.info(f"  OK  {m}")
                else:
                    tool_support_registry.mark(m, False)
                    logger.warning(
                        f"  NG  {m} - tool calling NOT supported (auto-excluded)"
                    )
            tool_support_registry.save()

    unsupported = tool_support_registry.unsupported_models()
    if unsupported:
        logger.warning(
            "以下のモデルはツール呼び出しに非対応のため自動除外されます "
            f"({len(unsupported)} 件): {unsupported}"
        )

    yield


app = FastAPI(title="OpenRouter Routing Proxy", lifespan=lifespan)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI 互換 chat completions エンドポイント"""
    payload = await request.json()
    stream = payload.get("stream", False)

    # クライアントからの model パラメータを削除（サーバー側で自動選択）
    payload.pop("model", None)

    models = []
    if model_router is not None:
        models = await model_router.get_free_models()
        models = tool_support_registry.filter_supported(models)

    if not models and not cloud_adapters:
        raise HTTPException(status_code=503, detail="No providers available")

    result = await failover_router.execute_with_failover(payload, models, stream)

    if stream:
        return StreamingResponse(result, media_type="text/event-stream")
    else:
        return result


@app.get("/health")
async def health():
    """ヘルスチェック"""
    return {"status": "ok"}
