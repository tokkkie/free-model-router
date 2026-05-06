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
    provider_name, list_models, chat_completion, chat_completion_stream を実装すること。
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """プロバイダー名を返す（例: "openrouter", "groq", "ollama"）"""
        pass

    @abstractmethod
    async def list_models(self) -> list[str]:
        """このプロバイダーの利用可能モデルリストを返す
        
        Returns:
            モデル ID のリスト（このプロバイダーで使用可能なもののみ）
        """
        pass

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
