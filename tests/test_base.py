import pytest
from typing import AsyncIterator

from adapters.base import (
    AbstractLLMAdapter,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
)


class TestExceptions:
    """例外クラスのテスト"""

    def test_rate_limit_error(self):
        """RateLimitError が正しく raise される"""
        with pytest.raises(RateLimitError, match="429"):
            raise RateLimitError("429 Too Many Requests")

    def test_provider_timeout_error(self):
        """ProviderTimeoutError が正しく raise される"""
        with pytest.raises(ProviderTimeoutError, match="timeout"):
            raise ProviderTimeoutError("Request timeout")

    def test_provider_error(self):
        """ProviderError が正しく raise される"""
        with pytest.raises(ProviderError, match="failed"):
            raise ProviderError("Request failed")


class TestAbstractLLMAdapter:
    """AbstractLLMAdapter のテスト"""

    def test_cannot_instantiate_abstract_class(self):
        """抽象クラスは直接インスタンス化できない"""
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            AbstractLLMAdapter()

    def test_concrete_implementation(self):
        """具象クラスは正しく実装できる"""

        class ConcreteAdapter(AbstractLLMAdapter):
            async def chat_completion(
                self, payload: dict, model: str, timeout: float
            ) -> dict:
                return {"result": "ok"}

            async def chat_completion_stream(
                self, payload: dict, model: str, timeout: float
            ) -> AsyncIterator[bytes]:
                yield b"data: test\n\n"

        adapter = ConcreteAdapter()
        assert adapter is not None

    @pytest.mark.asyncio
    async def test_concrete_adapter_methods(self):
        """具象クラスのメソッドが正しく動作する"""

        class ConcreteAdapter(AbstractLLMAdapter):
            async def chat_completion(
                self, payload: dict, model: str, timeout: float
            ) -> dict:
                return {"model": model, "payload": payload}

            async def chat_completion_stream(
                self, payload: dict, model: str, timeout: float
            ) -> AsyncIterator[bytes]:
                yield b"data: chunk1\n\n"
                yield b"data: chunk2\n\n"

        adapter = ConcreteAdapter()

        result = await adapter.chat_completion({"test": "data"}, "test-model", 10.0)
        assert result["model"] == "test-model"
        assert result["payload"] == {"test": "data"}

        chunks = []
        async for chunk in adapter.chat_completion_stream(
            {"test": "stream"}, "stream-model", 10.0
        ):
            chunks.append(chunk)
        assert len(chunks) == 2
        assert chunks[0] == b"data: chunk1\n\n"
