import json
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from adapters.ollama import OllamaAdapter
from adapters.openrouter import OpenRouterAdapter
from router.failover import FailoverRouter
from router.model_router import ModelRouter
from router.tool_support_registry import ToolSupportRegistry
from router.tool_verifier import verify_tool_support

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
if not openrouter_api_key:
    raise RuntimeError("OPENROUTER_API_KEY not set in environment")

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

ollama_adapter = OllamaAdapter(base_url=config["ollama_base_url"])

tool_support_registry = ToolSupportRegistry(
    cache_file=config.get("tool_support_cache_file", "tool_support_cache.json")
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Fetching free models from OpenRouter...")
    models = await model_router.get_free_models()
    logger.info(f"Found {len(models)} free models")

    pruned = tool_support_registry.prune(models)
    if pruned:
        logger.info(f"Pruned {pruned} stale models from tool support cache")

    if config.get("verify_tool_support", True):
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

    models = await model_router.get_free_models()
    models = tool_support_registry.filter_supported(models)
    if not models:
        raise HTTPException(status_code=503, detail="No free models available")

    failover = FailoverRouter(
        cloud_adapter=openrouter_adapter,
        local_adapter=ollama_adapter,
        local_model=config["ollama_model"],
        timeout=config["timeout_seconds"],
    )

    result = await failover.execute_with_failover(payload, models, stream)

    if stream:
        return StreamingResponse(result, media_type="text/event-stream")
    else:
        return result


@app.get("/health")
async def health():
    """ヘルスチェック"""
    return {"status": "ok"}
