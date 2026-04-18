import logging
from typing import AsyncIterator

from adapters.base import (
    AbstractLLMAdapter,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
)

logger = logging.getLogger(__name__)


class FailoverRouter:
    """429 / タイムアウト発生時に次のモデルへ自動切替を行う"""

    def __init__(
        self,
        cloud_adapter: AbstractLLMAdapter,
        local_adapter: AbstractLLMAdapter,
        local_model: str,
        timeout: float,
    ) -> None:
        self._cloud_adapter = cloud_adapter
        self._local_adapter = local_adapter
        self._local_model = local_model
        self._timeout = timeout

    async def execute_with_failover(
        self,
        payload: dict,
        models: list[str],
        stream: bool,
    ) -> dict | AsyncIterator[bytes]:
        """モデルリストを順に試行し、成功するまでリトライ"""
        if stream:
            return self._execute_stream_with_failover(payload, models)
        else:
            return await self._execute_non_stream_with_failover(payload, models)

    async def _execute_non_stream_with_failover(
        self, payload: dict, models: list[str]
    ) -> dict:
        """非ストリーミングリクエストのFailover処理"""
        last_error: Exception | None = None

        for model in models:
            try:
                logger.info(f"Trying model: {model}")
                return await self._cloud_adapter.chat_completion(
                    payload, model, self._timeout
                )
            except (RateLimitError, ProviderTimeoutError) as exc:
                logger.warning(f"Model {model} failed: {exc}")
                last_error = exc
                continue
            except ProviderError as exc:
                logger.error(f"Model {model} non-retryable error: {exc}")
                last_error = exc
                continue

        logger.warning("All cloud models failed, falling back to local Ollama")
        try:
            return await self._local_adapter.chat_completion(
                payload, self._local_model, self._timeout
            )
        except Exception as exc:
            logger.error(f"Local fallback failed: {exc}")
            raise ProviderError(
                f"All providers failed. Last error: {last_error}"
            ) from last_error

    async def _execute_stream_with_failover(
        self, payload: dict, models: list[str]
    ) -> AsyncIterator[bytes]:
        """ストリーミングリクエストのFailover処理"""
        last_error: Exception | None = None

        for model in models:
            try:
                logger.info(f"Trying model: {model}")
                stream_gen = self._cloud_adapter.chat_completion_stream(
                    payload, model, self._timeout
                )
                async for chunk in stream_gen:
                    yield chunk
                return
            except (RateLimitError, ProviderTimeoutError) as exc:
                logger.warning(f"Model {model} failed: {exc}")
                last_error = exc
                continue
            except ProviderError as exc:
                logger.error(f"Model {model} non-retryable error: {exc}")
                last_error = exc
                continue

        logger.warning("All cloud models failed, falling back to local Ollama")
        try:
            stream_gen = self._local_adapter.chat_completion_stream(
                payload, self._local_model, self._timeout
            )
            async for chunk in stream_gen:
                yield chunk
        except Exception as exc:
            logger.error(f"Local fallback failed: {exc}")
            raise ProviderError(
                f"All providers failed. Last error: {last_error}"
            ) from last_error
