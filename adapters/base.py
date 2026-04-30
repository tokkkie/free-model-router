from abc import ABC, abstractmethod
from typing import AsyncIterator


class RateLimitError(Exception):
    """429 Too Many Requests エラー"""


class ProviderTimeoutError(Exception):
    """タイムアウトエラー"""


class NotFoundError(Exception):
    """404 Not Found エラー"""


class ProviderError(Exception):
    """その他のプロバイダーエラー"""


class AbstractLLMAdapter(ABC):
    """LLM プロバイダーアダプターの基底クラス
    
    新しいプロバイダーを追加する場合はこのクラスを継承し、
    chat_completion と chat_completion_stream を実装すること。
    """

    @abstractmethod
    async def chat_completion(
        self,
        payload: dict,
        model: str,
        timeout: float,
    ) -> dict:
        """非ストリーミング補完（OpenAI 互換レスポンス）"""

    @abstractmethod
    async def chat_completion_stream(
        self,
        payload: dict,
        model: str,
        timeout: float,
    ) -> AsyncIterator[bytes]:
        """ストリーミング補完（SSE バイト列を yield）"""
