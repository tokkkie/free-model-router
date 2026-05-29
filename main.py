import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from adapters import ProviderFactory
from adapters.base import ProviderError
from router.failover import FailoverRouter
from router.model_router import ModelRouter
from router.preprocessor import TranslationPreprocessor
from router.tool_support_registry import ToolSupportRegistry
from router.tool_verifier import verify_tool_support

load_dotenv()

# ログレベルを環境変数で制御（デフォルト: INFO）
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

with open("config.yaml", encoding="utf-8") as f:
    config = yaml.safe_load(f)

# グローバル設定とプロバイダー設定を取得
global_config = config.get("global", {})
preprocess_config = config.get("preprocess", {})
enabled_providers = config.get("enabled_providers", [])
providers_config = config.get("providers", {})

# プロバイダーを enabled_providers の順序で初期化
cloud_adapters = []
local_adapter = None

logger.debug(f"Enabled providers: {enabled_providers}")

for provider_name in enabled_providers:
    provider_config = providers_config.get(provider_name)
    if not provider_config:
        logger.warning(f"Provider {provider_name} is enabled but not configured")
        continue
    
    api_key = os.getenv(f"{provider_name.upper()}_API_KEY")
    logger.debug(f"Initializing {provider_name} (API key: {'set' if api_key else 'not set'})")
    
    # OpenRouter は ModelRouter が必要
    kwargs = {}
    if provider_name == "openrouter":
        model_router = ModelRouter(
            openrouter_base_url=provider_config["base_url"],
            priority_keywords=provider_config.get("priority_keywords", []),
            exclude_keywords=provider_config.get("exclude_keywords", []),
            cache_ttl=global_config.get("model_cache_ttl_seconds", 300),
        )
        kwargs["model_router"] = model_router
    
    adapter = ProviderFactory.create(provider_name, provider_config, api_key, **kwargs)
    if adapter is None:
        logger.warning(f"Failed to create adapter for {provider_name}")
        continue
    
    logger.debug(f"Successfully created adapter for {provider_name}")
    
    # ローカルプロバイダーの判定
    if provider_name == "ollama":
        local_adapter = adapter
    else:
        cloud_adapters.append(adapter)

# FailoverRouter は起動時に local_adapter を設定するため、グローバル変数として保持
failover_router = None
translation_preprocessor: TranslationPreprocessor | None = None

# キャッシュディレクトリの作成
cache_dir = Path(global_config.get("cache_dir", ".cache"))
cache_dir.mkdir(exist_ok=True)

tool_support_registry = ToolSupportRegistry(
    cache_file=str(cache_dir / "tool_support_cache.json")
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global failover_router, local_adapter, translation_preprocessor

    # 翻訳プリプロセッサの初期化
    if preprocess_config.get("enable", False):
        ollama_config = providers_config.get("ollama", {})
        ollama_base_url = ollama_config.get("base_url", "http://localhost:11434")
        ollama_model = (
            preprocess_config.get("model")
            or ollama_config.get("model", "phi3:mini")
        )
        translate_timeout = float(preprocess_config.get("translate_timeout_seconds", 30))
        preprocessor = TranslationPreprocessor(
            base_url=ollama_base_url,
            model=ollama_model,
            timeout=translate_timeout,
        )
        if not await preprocessor.is_available():
            raise RuntimeError(
                f"Translation preprocessor is enabled but Ollama is not running at "
                f"{ollama_base_url}. Start Ollama or set preprocess.enable: false in config.yaml."
            )
        translation_preprocessor = preprocessor
        logger.info(f"Translation preprocessor enabled (model={ollama_model}, timeout={translate_timeout}s)")

    # Ollama の起動確認
    if local_adapter is not None:
        if await local_adapter.is_available():
            logger.info(f"Ollama available")
        else:
            logger.warning(f"Ollama not available, skipping local fallback")
            local_adapter = None

    # FailoverRouter を初期化
    failover_router = FailoverRouter(
        cloud_adapters=cloud_adapters,
        local_adapter=local_adapter,
        timeout=global_config.get("timeout_seconds", 15),
        cooldown_seconds=float(global_config.get("rate_limit_cooldown_seconds", 60)),
        not_found_cooldown_seconds=float(global_config.get("not_found_cooldown_seconds", 600)),
    )

    # 各プロバイダーのモデルリストを取得
    models_by_provider = {}
    for adapter in cloud_adapters:
        models = await adapter.list_models()
        models_by_provider[adapter.provider_name] = models
        logger.info(f"{adapter.provider_name}: {len(models)} models")

    if local_adapter:
        models = await local_adapter.list_models()
        models_by_provider[local_adapter.provider_name] = models

    # ツールサポート検証（OpenRouter のみ）
    openrouter_models = models_by_provider.get("openrouter", [])
    pruned = tool_support_registry.prune(openrouter_models) if openrouter_models else 0
    if pruned:
        logger.info(f"Pruned {pruned} stale models from tool support cache")

    # OpenRouter アダプターを取得
    openrouter_adapter = next((a for a in cloud_adapters if a.provider_name == "openrouter"), None)
    if global_config.get("verify_tool_support", True) and openrouter_adapter and openrouter_models:
        unverified = tool_support_registry.get_unverified(openrouter_models)
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

    # 各プロバイダーのモデルリストを取得
    models_by_provider = {}
    for adapter in cloud_adapters:
        models = await adapter.list_models()
        # OpenRouter のみツールサポートフィルタを適用
        if adapter.provider_name == "openrouter":
            models = tool_support_registry.filter_supported(models)
        models_by_provider[adapter.provider_name] = models

    if local_adapter:
        models = await local_adapter.list_models()
        models_by_provider[local_adapter.provider_name] = models

    if not models_by_provider:
        raise HTTPException(status_code=503, detail="No providers available")
    
    # 利用可能なモデルがあるかチェック（クールダウン中を除く）
    available_models = []
    for provider_name, models in models_by_provider.items():
        for model in models:
            if not failover_router._is_cooling_down(model):
                available_models.append(f"{provider_name}:{model}")
    
    if not available_models:
        raise HTTPException(
            status_code=503,
            detail="All models are currently on cooldown. Please try again later."
        )

    if translation_preprocessor is not None:
        try:
            payload = await translation_preprocessor.preprocess(payload)
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"Translation preprocessor failed: {str(e)}"
            )

    try:
        result = await failover_router.execute_with_failover(payload, models_by_provider, stream)
    except ProviderError as e:
        raise HTTPException(
            status_code=503,
            detail=f"All providers failed: {str(e)}"
        )

    if stream:
        return StreamingResponse(result, media_type="text/event-stream")
    else:
        return result


@app.get("/health")
async def health():
    """ヘルスチェック"""
    return {"status": "ok"}
