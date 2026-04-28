import logging
import time
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

    # 429 を返したモデルのクールダウン期限 (UNIX秒)。
    # FailoverRouter はリクエストごとに再インスタンス化されるため
    # クラス変数でプロセス内共有する（再起動でリセット）
    _cooldown_until: dict[str, float] = {}

    def __init__(
        self,
        cloud_adapter: AbstractLLMAdapter,
        local_adapter: AbstractLLMAdapter,
        local_model: str,
        timeout: float,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self._cloud_adapter = cloud_adapter
        self._local_adapter = local_adapter
        self._local_model = local_model
        self._timeout = timeout
        self._cooldown_seconds = cooldown_seconds

    def _is_cooling_down(self, model: str) -> bool:
        """モデルがクールダウン期間中かを判定"""
        expires = self._cooldown_until.get(model)
        if expires is None:
            return False
        if time.monotonic() >= expires:
            # 期限切れエントリを掃除
            self._cooldown_until.pop(model, None)
            return False
        return True

    def _mark_cooldown(self, model: str) -> None:
        """モデルを cooldown_seconds 秒間スキップ対象にする"""
        if self._cooldown_seconds <= 0:
            return
        self._cooldown_until[model] = time.monotonic() + self._cooldown_seconds
        logger.info(f"COOLDOWN {self._cooldown_seconds:.0f}s   {model}")

    def _filter_available(self, models: list[str]) -> list[str]:
        """クールダウン中のモデルを除外"""
        available = [m for m in models if not self._is_cooling_down(m)]
        skipped = len(models) - len(available)
        if skipped:
            logger.info(f"SKIP {skipped} on cooldown")
        return available

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
        available_models = self._filter_available(models)

        for model in available_models:
            try:
                result = await self._cloud_adapter.chat_completion(
                    payload, model, self._timeout
                )
                logger.info(f"200 OK   {model}")
                return result
            except RateLimitError as exc:
                logger.warning(f"429 Rate limit   {model}")
                self._mark_cooldown(model)
                last_error = exc
                continue
            except ProviderTimeoutError as exc:
                logger.warning(f"TIMEOUT   {model}")
                last_error = exc
                continue
            except ProviderError as exc:
                logger.error(f"{exc.status_code if hasattr(exc, 'status_code') else 'ERROR'} {str(exc)[:50]}   {model}")
                last_error = exc
                continue

        logger.warning("FALLBACK local")
        try:
            result = await self._local_adapter.chat_completion(
                payload, self._local_model, self._timeout
            )
            logger.info(f"200 OK (local)   {self._local_model}")
            return result
        except Exception as exc:
            logger.error(f"FAIL local   {self._local_model}")
            raise ProviderError(
                f"All providers failed. Last error: {last_error}"
            ) from last_error

    async def _execute_stream_with_failover(
        self, payload: dict, models: list[str]
    ) -> AsyncIterator[bytes]:
        """ストリーミングリクエストのFailover処理"""
        last_error: Exception | None = None
        available_models = self._filter_available(models)

        for model in available_models:
            try:
                stream_gen = self._cloud_adapter.chat_completion_stream(
                    payload, model, self._timeout
                )
                async for chunk in stream_gen:
                    yield chunk
                logger.info(f"200 OK (stream)   {model}")
                return
            except RateLimitError as exc:
                logger.warning(f"429 Rate limit   {model}")
                self._mark_cooldown(model)
                last_error = exc
                continue
            except ProviderTimeoutError as exc:
                logger.warning(f"TIMEOUT   {model}")
                last_error = exc
                continue
            except ProviderError as exc:
                logger.error(f"{exc.status_code if hasattr(exc, 'status_code') else 'ERROR'} {str(exc)[:50]}   {model}")
                last_error = exc
                continue

        logger.warning("FALLBACK local")
        try:
            stream_gen = self._local_adapter.chat_completion_stream(
                payload, self._local_model, self._timeout
            )
            async for chunk in stream_gen:
                yield chunk
            logger.info(f"200 OK (stream local)   {self._local_model}")
        except Exception as exc:
            logger.error(f"FAIL local   {self._local_model}")
            raise ProviderError(
                f"All providers failed. Last error: {last_error}"
            ) from last_error
