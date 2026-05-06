import json
import logging
from typing import AsyncGenerator

import httpx

from .base import AbstractLLMAdapter, NotFoundError, ProviderError, ProviderTimeoutError, RateLimitError

logger = logging.getLogger(__name__)

_MIN_CONTEXT_WINDOW = 120000
_MIN_MAX_COMPLETION_TOKENS = 30000


class CerebrasAdapter(AbstractLLMAdapter):
    """Cerebras API アダプター（OpenAI 互換）"""

    def __init__(self, api_key: str, base_url: str = "https://api.cerebras.ai/v1") -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._available_models: list[str] = []

    @property
    def provider_name(self) -> str:
        return "cerebras"

    @classmethod
    def from_config(cls, config: dict, api_key: str | None):
        """config から CerebrasAdapter を生成"""
        if not api_key:
            logger.warning("CEREBRAS_API_KEY not set")
            return None
        return cls(
            api_key=api_key,
            base_url=config.get("base_url", "https://api.cerebras.ai/v1")
        )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def list_models(self) -> list[str]:
        """Cerebras で利用可能なモデル一覧を取得"""
        if self._available_models:
            return self._available_models

        try:
            # モデルリストを取得
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._base_url}/models",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning(f"Failed to fetch Cerebras models: {exc}")
            return []

        model_ids = [m["id"] for m in data.get("data", [])]
        
        # 各モデルの詳細情報を取得してフィルタリング
        usable_models = []
        for model_id in model_ids:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    detail_resp = await client.get(
                        f"https://api.cerebras.ai/public/v1/models/{model_id}",
                        headers=self._headers(),
                    )
                    detail_resp.raise_for_status()
                    detail = detail_resp.json()
                
                # フィルタリング条件
                capabilities = detail.get("capabilities", {})
                architecture = detail.get("architecture", {})
                limits = detail.get("limits", {})
                
                if (
                    capabilities.get("tools") is True
                    and capabilities.get("tool_choice") is True
                    and architecture.get("modality") == "text"
                    and limits.get("max_context_length", 0) >= _MIN_CONTEXT_WINDOW
                    and limits.get("max_completion_tokens", 0) >= _MIN_MAX_COMPLETION_TOKENS
                ):
                    usable_models.append({
                        "id": model_id,
                        "ctx": limits.get("max_context_length"),
                        "max_out": limits.get("max_completion_tokens")
                    })
            except Exception as exc:
                logger.warning(f"Failed to fetch details for {model_id}: {exc}")
                continue

        self._available_models = [m["id"] for m in usable_models]
        logger.info(
            f"Cerebras models: {len(model_ids)} total, "
            f"{len(self._available_models)} usable "
            f"(context_window>={_MIN_CONTEXT_WINDOW}, "
            f"max_completion_tokens>={_MIN_MAX_COMPLETION_TOKENS}, "
            f"tools=true, modality=text)"
        )
        for m in usable_models:
            logger.info(f"  {m['id']} (ctx={m['ctx']}, max_out={m['max_out']})")
        
        return self._available_models

    async def chat_completion(self, payload: dict, model: str, timeout: float) -> dict:
        """非ストリーミング補完"""
        body = {**payload, "model": model, "stream": False}
        
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Cerebras timeout ({model})") from exc

        # レートリミットエラー
        if resp.status_code == 429:
            raise RateLimitError(f"Cerebras 429 ({model})")
        
        # モデル未検出エラー
        if resp.status_code == 404:
            raise NotFoundError(f"Cerebras 404 ({model})")
        
        # その他のエラー
        if not resp.is_success:
            error_text = resp.text
            # Cerebras 固有のエラーメッセージを解析
            try:
                error_data = resp.json()
                error_msg = error_data.get("message", error_text[:200])
                error_type = error_data.get("type", "")
                
                # キュー超過エラーもレートリミットとして扱う
                if error_type == "too_many_requests_error":
                    raise RateLimitError(f"Cerebras rate limit ({model}): {error_msg}")
            except json.JSONDecodeError:
                pass
            
            raise ProviderError(f"Cerebras {resp.status_code} ({model}): {error_text[:200]}")

        return resp.json()

    async def chat_completion_stream(
        self, payload: dict, model: str, timeout: float
    ) -> AsyncGenerator[bytes, None]:
        """ストリーミング補完"""
        body = {**payload, "model": model, "stream": True}
        client = httpx.AsyncClient(timeout=timeout)
        
        try:
            req = client.build_request(
                "POST",
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=body,
            )
            resp = await client.send(req, stream=True)

            # レートリミットエラー
            if resp.status_code == 429:
                raise RateLimitError(f"Cerebras 429 ({model})")
            
            # モデル未検出エラー
            if resp.status_code == 404:
                raise NotFoundError(f"Cerebras 404 ({model})")

            if not resp.is_success:
                err = await resp.aread()
                try:
                    error_data = json.loads(err)
                    error_msg = error_data.get("message", err[:200])
                    error_type = error_data.get("type", "")
                    
                    if error_type == "too_many_requests_error":
                        raise RateLimitError(f"Cerebras rate limit ({model}): {error_msg}")
                except json.JSONDecodeError:
                    pass
                
                raise ProviderError(f"Cerebras {resp.status_code} ({model}): {err[:200]}")

            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Cerebras timeout ({model})") from exc
        finally:
            await client.aclose()
