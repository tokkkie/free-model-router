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
    cache_ttl=config["model_cache_ttl_seconds"],
)

openrouter_adapter = OpenRouterAdapter(
    api_key=openrouter_api_key,
    base_url=config["openrouter_base_url"],
)

ollama_adapter = OllamaAdapter(base_url=config["ollama_base_url"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Fetching free models from OpenRouter...")
    models = await model_router.get_free_models()
    logger.info(f"Found {len(models)} free models")
    yield


app = FastAPI(title="OpenRouter Routing Proxy", lifespan=lifespan)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI 互換 chat completions エンドポイント"""
    payload = await request.json()
    stream = payload.get("stream", False)

    models = await model_router.get_free_models()
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
