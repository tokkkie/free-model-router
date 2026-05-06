import json
import logging
import time
from typing import AsyncIterator

from adapters.base import (
    AbstractLLMAdapter,
    NotFoundError,
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
        cloud_adapters: list[AbstractLLMAdapter],
        local_adapter: AbstractLLMAdapter | None,
        timeout: float,
        cooldown_seconds: float = 60.0,
        not_found_cooldown_seconds: float = 600.0,
    ) -> None:
        self._cloud_adapters = cloud_adapters
        self._local_adapter = local_adapter
        self._timeout = timeout
        self._cooldown_seconds = cooldown_seconds
        self._not_found_cooldown_seconds = not_found_cooldown_seconds

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

    def _mark_cooldown(self, model: str, duration: float | None = None) -> None:
        """モデルを指定秒数間スキップ対象にする"""
        if duration is None:
            duration = self._cooldown_seconds
        if duration <= 0:
            return
        self._cooldown_until[model] = time.monotonic() + duration
        logger.info(f"COOLDOWN {duration:.0f}s   {model}")

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
        models_by_provider: dict[str, list[str]],
        stream: bool,
    ) -> dict | AsyncIterator[bytes]:
        """フェイルオーバー付きでリクエストを実行
        
        Args:
            payload: リクエストペイロード
            models_by_provider: プロバイダー名をキーとしたモデルリストの辞書
            stream: ストリーミングモードかどうか
        """
        if stream:
            return self._execute_stream_with_failover(payload, models_by_provider)
        else:
            return await self._execute_non_stream_with_failover(payload, models_by_provider)

    async def _execute_non_stream_with_failover(
        self, payload: dict, models_by_provider: dict[str, list[str]]
    ) -> dict:
        """非ストリーミングリクエストのFailover処理"""
        last_error: Exception | None = None

        # 各クラウドアダプターを順に試行
        for adapter in self._cloud_adapters:
            models = models_by_provider.get(adapter.provider_name, [])
            available_models = self._filter_available(models)

            for model in available_models:
                try:
                    result = await adapter.chat_completion(
                        payload, model, self._timeout
                    )
                    logger.info(f"200 OK   {model}")
                    return result
                except RateLimitError as exc:
                    logger.warning(f"429 Rate limit   {model}")
                    self._mark_cooldown(model)
                    last_error = exc
                    continue
                except NotFoundError as exc:
                    logger.warning(f"404 Not Found   {model}")
                    self._mark_cooldown(model, self._not_found_cooldown_seconds)
                    last_error = exc
                    continue
                except ProviderTimeoutError as exc:
                    logger.warning(f"TIMEOUT   {model}")
                    last_error = exc
                    continue
                except ProviderError as exc:
                    logger.error(f"ERROR {str(exc)[:50]}   {model}")
                    last_error = exc
                    continue

        # ローカルフォールバック
        if self._local_adapter is not None:
            logger.warning("FALLBACK local")
            local_models = models_by_provider.get(self._local_adapter.provider_name, [])
            if local_models:
                try:
                    result = await self._local_adapter.chat_completion(
                        payload, local_models[0], self._timeout
                    )
                    logger.info(f"200 OK (local)   {local_models[0]}")
                    return result
                except Exception as exc:
                    logger.error(f"FAIL local   {self._local_adapter.provider_name}")
        
        raise ProviderError(
            f"All providers failed. Last error: {last_error}"
        )

    async def _execute_stream_with_failover(
        self, payload: dict, models_by_provider: dict[str, list[str]]
    ) -> AsyncIterator[bytes]:
        """ストリーミングリクエストのFailover処理"""
        last_error: Exception | None = None

        # 各クラウドアダプターを順に試行
        for adapter in self._cloud_adapters:
            models = models_by_provider.get(adapter.provider_name, [])
            available_models = self._filter_available(models)

            for model in available_models:
                try:
                    stream_gen = adapter.chat_completion_stream(
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
                except NotFoundError as exc:
                    logger.warning(f"404 Not Found   {model}")
                    self._mark_cooldown(model, self._not_found_cooldown_seconds)
                    last_error = exc
                    continue
                except ProviderTimeoutError as exc:
                    logger.warning(f"TIMEOUT   {model}")
                    last_error = exc
                    continue
                except ProviderError as exc:
                    logger.error(f"ERROR {str(exc)[:50]}   {model}")
                    last_error = exc
                    continue

        # ローカルフォールバック
        if self._local_adapter is not None:
            logger.warning("FALLBACK local")
            local_models = models_by_provider.get(self._local_adapter.provider_name, [])
            if local_models:
                try:
                    stream_gen = self._local_adapter.chat_completion_stream(
                        payload, local_models[0], self._timeout
                    )
                    async for chunk in stream_gen:
                        yield chunk
                    logger.info(f"200 OK (stream local)   {local_models[0]}")
                    return
                except Exception as exc:
                    logger.error(f"FAIL local   {self._local_adapter.provider_name}")

        # 全プロバイダー失敗時はSSEエラーメッセージを返す
        error_msg = f"All providers failed. Last error: {last_error}"
        logger.error(error_msg)
        error_data = {
            "error": {
                "message": error_msg,
                "type": "provider_error",
                "code": "all_providers_failed"
            }
        }
        yield f"data: {json.dumps(error_data)}\n\n".encode()
        yield b"data: [DONE]\n\n"
