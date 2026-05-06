import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
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

with open("config.yaml", encoding="utf-8") as f:
    config = yaml.safe_load(f)

# グローバル設定とプロバイダー設定を取得
global_config = config.get("global", {})
providers_config = config.get("providers", {})

# プロバイダーの初期化（後で lifespan で使用）
model_router = None
openrouter_adapter = None
groq_adapter = None
ollama_adapter = None
cloud_adapters = []

# OpenRouter の初期化
openrouter_config = providers_config.get("openrouter", {})
if openrouter_config.get("enabled", False):
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_api_key:
        model_router = ModelRouter(
            openrouter_base_url=openrouter_config["base_url"],
            priority_keywords=openrouter_config.get("priority_keywords", []),
            exclude_keywords=openrouter_config.get("exclude_keywords", []),
            cache_ttl=global_config.get("model_cache_ttl_seconds", 300),
        )
        openrouter_adapter = OpenRouterAdapter(
            api_key=openrouter_api_key,
            base_url=openrouter_config["base_url"],
        )
        cloud_adapters.append(openrouter_adapter)
        logger.info("OpenRouter provider enabled")
    else:
        logger.warning("OpenRouter enabled in config but OPENROUTER_API_KEY not set")

# Groq の初期化
groq_config = providers_config.get("groq", {})
if groq_config.get("enabled", False):
    groq_api_key = os.getenv("GROQ_API_KEY")
    if groq_api_key:
        groq_adapter = GroqAdapter(
            api_key=groq_api_key,
            base_url=groq_config.get("base_url", "https://api.groq.com/openai/v1")
        )
        cloud_adapters.append(groq_adapter)
        logger.info("Groq provider enabled")
    else:
        logger.warning("Groq enabled in config but GROQ_API_KEY not set")

# Ollama の初期化
ollama_config = providers_config.get("ollama", {})
if ollama_config.get("enabled", False):
    ollama_adapter = OllamaAdapter(base_url=ollama_config["base_url"])

# FailoverRouter は起動時に local_adapter を設定するため、グローバル変数として保持
failover_router = None

# キャッシュディレクトリの作成
cache_dir = Path(global_config.get("cache_dir", ".cache"))
cache_dir.mkdir(exist_ok=True)

tool_support_registry = ToolSupportRegistry(
    cache_file=str(cache_dir / "tool_support_cache.json")
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global failover_router

    # Ollama の起動確認
    local_adapter = None
    local_model = None
    if ollama_adapter is not None:
        local_model = ollama_config.get("model", "phi3.5:latest")
        if await ollama_adapter.is_available():
            local_adapter = ollama_adapter
            logger.info(f"Ollama available at {ollama_config['base_url']}")
        else:
            logger.warning(f"Ollama not available at {ollama_config['base_url']}, skipping local fallback")

    # FailoverRouter を初期化
    failover_router = FailoverRouter(
        cloud_adapters=cloud_adapters,
        local_adapter=local_adapter,
        local_model=local_model,
        timeout=global_config.get("timeout_seconds", 15),
        cooldown_seconds=float(global_config.get("rate_limit_cooldown_seconds", 60)),
        not_found_cooldown_seconds=float(global_config.get("not_found_cooldown_seconds", 600)),
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

    if global_config.get("verify_tool_support", True) and openrouter_adapter and models:
        unverified = tool_support_registry.get_unverified(models)
        if unverified:
            logger.info(
                f"{len(unverified)} new models detected, verifying tool support..."
            )
            verify_timeout = float(global_config.get("verify_timeout_seconds", 15))
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
