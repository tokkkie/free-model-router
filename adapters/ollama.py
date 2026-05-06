import logging
from typing import AsyncGenerator

import httpx

from .base import AbstractLLMAdapter, ProviderError, ProviderTimeoutError

logger = logging.getLogger(__name__)


class OllamaAdapter(AbstractLLMAdapter):
    """Ollama ローカルインスタンス用アダプター（OpenAI 互換エンドポイント使用）"""

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def list_models(self) -> list[str]:
        """Ollama は単一モデルのみ"""
        return [self._model]

    @classmethod
    def from_config(cls, config: dict, api_key: str | None):
        """config から OllamaAdapter を生成"""
        return cls(
            base_url=config.get("base_url", "http://localhost:11434"),
            model=config.get("model", "phi3.5:latest")
        )

    def _headers(self) -> dict:
        return {"Content-Type": "application/json"}

    async def is_available(self) -> bool:
        """Ollama が起動しているか確認"""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                return resp.is_success
        except Exception:
            return False

    async def chat_completion(self, payload: dict, model: str, timeout: float) -> dict:
        body = {**payload, "model": model, "stream": False}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Ollama timeout ({model})") from exc
        except httpx.ConnectError as exc:
            raise ProviderError(f"Ollama connection failed: {exc}") from exc

        if not resp.is_success:
            raise ProviderError(f"Ollama {resp.status_code} ({model}): {resp.text[:200]}")
        return resp.json()

    async def chat_completion_stream(
        self, payload: dict, model: str, timeout: float
    ) -> AsyncGenerator[bytes, None]:
        body = {**payload, "model": model, "stream": True}
        client = httpx.AsyncClient(timeout=timeout)
        try:
            req = client.build_request(
                "POST",
                f"{self._base_url}/v1/chat/completions",
                headers=self._headers(),
                json=body,
            )
            resp = await client.send(req, stream=True)

            if not resp.is_success:
                err = await resp.aread()
                raise ProviderError(f"Ollama {resp.status_code} ({model}): {err[:200]}")

            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Ollama timeout ({model})") from exc
        except httpx.ConnectError as exc:
            raise ProviderError(f"Ollama connection failed: {exc}") from exc
        finally:
            await client.aclose()
