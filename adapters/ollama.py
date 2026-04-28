import logging
from typing import AsyncGenerator

import httpx

from .base import AbstractLLMAdapter, ProviderError, ProviderTimeoutError

logger = logging.getLogger(__name__)


class OllamaAdapter(AbstractLLMAdapter):
    """Ollama ローカルインスタンス用アダプター（OpenAI 互換エンドポイント使用）"""

    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict:
        return {"Content-Type": "application/json"}

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
