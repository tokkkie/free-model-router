import logging
from typing import Type

from .base import AbstractLLMAdapter
from .openrouter import OpenRouterAdapter
from .groq import GroqAdapter
from .ollama import OllamaAdapter
from .cerebras import CerebrasAdapter

logger = logging.getLogger(__name__)


class ProviderFactory:
    """プロバイダーを動的に生成するファクトリー"""

    _registry: dict[str, Type[AbstractLLMAdapter]] = {}

    @classmethod
    def register(cls, name: str, adapter_class: Type[AbstractLLMAdapter]):
        """プロバイダーを登録"""
        cls._registry[name] = adapter_class
        logger.debug(f"Registered provider: {name}")

    @classmethod
    def create(cls, name: str, config: dict, api_key: str | None, **kwargs) -> AbstractLLMAdapter | None:
        """設定からプロバイダーを生成

        Args:
            name: プロバイダー名
            config: プロバイダー設定
            api_key: API キー（環境変数から取得）
            **kwargs: プロバイダー固有の追加引数（例: model_router）

        Returns:
            生成されたアダプター、または None（無効化されている場合）
        """
        if not config.get("enabled", False):
            logger.debug(f"Provider {name} is disabled")
            return None

        adapter_class = cls._registry.get(name)
        if not adapter_class:
            logger.warning(f"Unknown provider: {name}")
            return None

        try:
            adapter = adapter_class.from_config(config, api_key, **kwargs)
            if adapter:
                logger.info(f"{name.capitalize()} provider enabled")
            return adapter
        except Exception as e:
            logger.error(f"Failed to create {name} provider: {e}")
            return None


# プロバイダーを自動登録
ProviderFactory.register("openrouter", OpenRouterAdapter)
ProviderFactory.register("groq", GroqAdapter)
ProviderFactory.register("cerebras", CerebrasAdapter)
ProviderFactory.register("ollama", OllamaAdapter)
