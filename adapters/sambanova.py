import logging
from typing import AsyncGenerator

import httpx

from .base import AbstractLLMAdapter, ProviderError, ProviderTimeoutError, RateLimitError, NotFoundError

logger = logging.getLogger(__name__)


class SambaNovaAdapter(AbstractLLMAdapter):
    """SambaNova API アダプター（OpenAI 互換）"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.sambanova.ai/v1",
        min_context_window: int = 128000,
        min_max_completion_tokens: int = 8192
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._min_context_window = min_context_window
        self._min_max_completion_tokens = min_max_completion_tokens
        self._available_models: list[str] = []

    @property
    def provider_name(self) -> str:
        return "sambanova"

    @classmethod
    def from_config(cls, config: dict, api_key: str | None, **kwargs) -> "SambaNovaAdapter | None":
        """設定から SambaNova アダプターを生成"""
        if not api_key:
            logger.warning("SAMBANOVA_API_KEY not set")
            return None

        return cls(
            api_key=api_key,
            base_url=config.get("base_url", "https://api.sambanova.ai/v1"),
            min_context_window=config.get("min_context_window", 128000),
            min_max_completion_tokens=config.get("min_max_completion_tokens", 8192),
        )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def list_models(self) -> list[str]:
        """SambaNova で利用可能なモデル一覧を取得してフィルタリング"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._base_url}/models",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning(f"Failed to fetch SambaNova models: {exc}")
            return []

        all_models = data.get("data", [])
        
        # フィルタリング
        filtered = []
        for m in all_models:
            model_id = m.get("id", "")
            context_length = m.get("context_length", 0)
            max_completion_tokens = m.get("max_completion_tokens", 0)

            # 条件チェック
            if context_length < self._min_context_window:
                logger.debug(f"Skipping {model_id}: context_length {context_length} < {self._min_context_window}")
                continue

            if max_completion_tokens < self._min_max_completion_tokens:
                logger.debug(f"Skipping {model_id}: max_completion_tokens {max_completion_tokens} < {self._min_max_completion_tokens}")
                continue

            # モデル名に "base" や "-cb" を含まない
            if "base" in model_id.lower() or "-cb" in model_id.lower():
                logger.debug(f"Skipping {model_id}: contains 'base' or '-cb'")
                continue

            filtered.append(m)

        # pricing.completion が大きい順にソート
        filtered.sort(
            key=lambda m: float(m.get("pricing", {}).get("completion", 0)),
            reverse=True
        )

        self._available_models = [m["id"] for m in filtered]
        
        logger.info(
            f"SambaNova models: {len(all_models)} total, "
            f"{len(self._available_models)} usable "
            f"(context_length>={self._min_context_window}, "
            f"max_completion_tokens>={self._min_max_completion_tokens})"
        )
        for m in filtered:
            pricing = m.get("pricing", {}).get("completion", "N/A")
            logger.info(f"  {m['id']} (ctx={m.get('context_length')}, max_out={m.get('max_completion_tokens')}, price={pricing})")
        
        return self._available_models

    async def chat_completion(self, payload: dict, model: str, timeout: float) -> dict:
        models_to_try = [model] if model else self._available_models
        last_error = None

        for m in models_to_try:
            body = {**payload, "model": m, "stream": False}

            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{self._base_url}/chat/completions",
                        headers=self._headers(),
                        json=body,
                    )
            except httpx.TimeoutException:
                last_error = ProviderTimeoutError(f"SambaNova timeout ({m})")
                continue

            if resp.status_code == 429:
                last_error = RateLimitError(f"SambaNova 429 ({m})")
                continue
            if resp.status_code == 404:
                last_error = NotFoundError(f"SambaNova 404 ({m})")
                continue
            if not resp.is_success:
                last_error = ProviderError(f"SambaNova {resp.status_code} ({m}): {resp.text[:200]}")
                continue

            return resp.json()

        if last_error:
            raise last_error
        raise ProviderError("SambaNova: No models available")

    async def chat_completion_stream(
        self, payload: dict, model: str, timeout: float
    ) -> AsyncGenerator[bytes, None]:
        models_to_try = [model] if model else self._available_models
        last_error = None

        for m in models_to_try:
            body = {**payload, "model": m, "stream": True}

            client = httpx.AsyncClient(timeout=timeout)
            try:
                req = client.build_request(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
                resp = await client.send(req, stream=True)

                if resp.status_code == 429:
                    await resp.aclose()
                    await client.aclose()
                    last_error = RateLimitError(f"SambaNova 429 ({m})")
                    continue
                if resp.status_code == 404:
                    await resp.aclose()
                    await client.aclose()
                    last_error = NotFoundError(f"SambaNova 404 ({m})")
                    continue
                if not resp.is_success:
                    err = await resp.aread()
                    await client.aclose()
                    last_error = ProviderError(f"SambaNova {resp.status_code} ({m}): {err[:200]}")
                    continue

                try:
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            yield chunk
                    return
                finally:
                    await client.aclose()

            except httpx.TimeoutException:
                await client.aclose()
                last_error = ProviderTimeoutError(f"SambaNova timeout ({m})")
                continue

        if last_error:
            raise last_error
        raise ProviderError("SambaNova: No models available")
