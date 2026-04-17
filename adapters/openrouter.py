from typing import AsyncGenerator

import httpx

from .base import AbstractLLMAdapter, ProviderError, ProviderTimeoutError, RateLimitError


class OpenRouterAdapter(AbstractLLMAdapter):
    """OpenRouter API アダプター"""

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1") -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "openrouter-routing",
        }

    async def chat_completion(self, payload: dict, model: str, timeout: float) -> dict:
        body = {**payload, "model": model, "stream": False}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"OpenRouter timeout ({model})") from exc

        if resp.status_code == 429:
            raise RateLimitError(f"OpenRouter 429 ({model})")
        if not resp.is_success:
            raise ProviderError(f"OpenRouter {resp.status_code} ({model}): {resp.text[:200]}")
        return resp.json()

    async def chat_completion_stream(
        self, payload: dict, model: str, timeout: float
    ) -> AsyncGenerator[bytes, None]:
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

            if resp.status_code == 429:
                await resp.aclose()
                raise RateLimitError(f"OpenRouter 429 ({model})")
            if not resp.is_success:
                err = await resp.aread()
                raise ProviderError(f"OpenRouter {resp.status_code} ({model}): {err[:200]}")

            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"OpenRouter timeout ({model})") from exc
        finally:
            await client.aclose()
