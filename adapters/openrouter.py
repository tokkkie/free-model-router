import json
import logging
from typing import AsyncGenerator

import httpx

from .base import AbstractLLMAdapter, NotFoundError, ProviderError, ProviderTimeoutError, RateLimitError

logger = logging.getLogger(__name__)


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
        if resp.status_code == 404:
            raise NotFoundError(f"OpenRouter 404 ({model})")
        if not resp.is_success:
            raise ProviderError(f"OpenRouter {resp.status_code} ({model}): {resp.text[:200]}")

        response = resp.json()
        return self._normalize_response(response)

    def _normalize_response(self, response: dict) -> dict:
        """OpenRouter 固有のフィールドを削除して OpenAI 互換にする"""
        if "choices" in response and len(response["choices"]) > 0:
            message = response["choices"][0].get("message", {})
            # OpenRouter 固有のフィールドを削除
            message.pop("reasoning", None)
            message.pop("reasoning_details", None)
        return response

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
            if resp.status_code == 404:
                await resp.aclose()
                raise NotFoundError(f"OpenRouter 404 ({model})")
            if not resp.is_success:
                err = await resp.aread()
                raise ProviderError(f"OpenRouter {resp.status_code} ({model}): {err[:200]}")

            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]  # "data: " を削除
                    if data_str == "[DONE]":
                        yield b"data: [DONE]\n\n"
                        continue
                    try:
                        data = json.loads(data_str)
                        data = self._normalize_stream_chunk(data)
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
                    except json.JSONDecodeError:
                        yield f"{line}\n\n".encode("utf-8")
                elif line:
                    yield f"{line}\n\n".encode("utf-8")
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"OpenRouter timeout ({model})") from exc
        finally:
            await client.aclose()

    def _normalize_stream_chunk(self, chunk: dict) -> dict:
        """ストリーミングチャンクから OpenRouter 固有のフィールドを削除"""
        if "choices" in chunk and len(chunk["choices"]) > 0:
            delta = chunk["choices"][0].get("delta", {})
            # OpenRouter 固有のフィールドを削除
            delta.pop("reasoning", None)
            delta.pop("reasoning_details", None)
        return chunk
