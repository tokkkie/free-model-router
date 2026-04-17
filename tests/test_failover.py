import sys
import pytest
from unittest.mock import AsyncMock, MagicMock

# モジュールを強制的に再読み込み（他のテストでのモックの影響を排除）
for mod in list(sys.modules.keys()):
    if mod.startswith('adapters.') or mod.startswith('router.'):
        del sys.modules[mod]

from adapters.base import (
    AbstractLLMAdapter,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
)
from router.failover import FailoverRouter


class TestFailoverRouter:
    """FailoverRouter のテスト"""

    def test_initialization(self):
        """初期化が正しく行われる"""
        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        local_adapter = MagicMock(spec=AbstractLLMAdapter)
        
        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
        )
        
        assert router._cloud_adapter == cloud_adapter
        assert router._local_adapter == local_adapter
        assert router._local_model == "phi3.5:latest"
        assert router._timeout == 20.0

    @pytest.mark.asyncio
    async def test_execute_with_failover_success_first_model(self):
        """最初のモデルで成功する"""
        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        cloud_adapter.chat_completion = AsyncMock(return_value={"result": "success"})
        local_adapter = MagicMock(spec=AbstractLLMAdapter)
        
        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
        )
        
        result = await router.execute_with_failover(
            payload={"messages": []},
            models=["model1", "model2"],
            stream=False,
        )
        
        assert result == {"result": "success"}
        assert cloud_adapter.chat_completion.call_count == 1

    @pytest.mark.asyncio
    async def test_execute_with_failover_retry_on_rate_limit(self):
        """429 エラー時に次のモデルへリトライする"""
        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        cloud_adapter.chat_completion = AsyncMock(
            side_effect=[
                RateLimitError("429"),
                {"result": "success"},
            ]
        )
        local_adapter = MagicMock(spec=AbstractLLMAdapter)
        
        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
        )
        
        result = await router.execute_with_failover(
            payload={"messages": []},
            models=["model1", "model2"],
            stream=False,
        )
        
        assert result == {"result": "success"}
        assert cloud_adapter.chat_completion.call_count == 2

    @pytest.mark.asyncio
    async def test_execute_with_failover_retry_on_timeout(self):
        """タイムアウト時に次のモデルへリトライする"""
        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        cloud_adapter.chat_completion = AsyncMock(
            side_effect=[
                ProviderTimeoutError("timeout"),
                {"result": "success"},
            ]
        )
        local_adapter = MagicMock(spec=AbstractLLMAdapter)
        
        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
        )
        
        result = await router.execute_with_failover(
            payload={"messages": []},
            models=["model1", "model2"],
            stream=False,
        )
        
        assert result == {"result": "success"}
        assert cloud_adapter.chat_completion.call_count == 2

    @pytest.mark.asyncio
    async def test_execute_with_failover_fallback_to_local(self):
        """全クラウドモデル失敗時にローカルへフォールバックする"""
        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        cloud_adapter.chat_completion = AsyncMock(
            side_effect=RateLimitError("429")
        )
        local_adapter = MagicMock(spec=AbstractLLMAdapter)
        local_adapter.chat_completion = AsyncMock(return_value={"result": "local"})
        
        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
        )
        
        result = await router.execute_with_failover(
            payload={"messages": []},
            models=["model1", "model2"],
            stream=False,
        )
        
        assert result == {"result": "local"}
        assert cloud_adapter.chat_completion.call_count == 2
        assert local_adapter.chat_completion.call_count == 1

    @pytest.mark.asyncio
    async def test_execute_with_failover_all_failed(self):
        """全プロバイダー失敗時に ProviderError が raise される"""
        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        cloud_adapter.chat_completion = AsyncMock(
            side_effect=RateLimitError("429")
        )
        local_adapter = MagicMock(spec=AbstractLLMAdapter)
        local_adapter.chat_completion = AsyncMock(
            side_effect=ProviderError("local failed")
        )
        
        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
        )
        
        with pytest.raises(ProviderError, match="All providers failed"):
            await router.execute_with_failover(
                payload={"messages": []},
                models=["model1"],
                stream=False,
            )

    @pytest.mark.asyncio
    async def test_execute_with_failover_stream_success(self):
        """ストリーミング時も正しく動作する"""
        async def mock_stream():
            yield b"data: chunk\n\n"
        
        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        cloud_adapter.chat_completion_stream = MagicMock(return_value=mock_stream())
        local_adapter = MagicMock(spec=AbstractLLMAdapter)
        
        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
        )
        
        result = await router.execute_with_failover(
            payload={"messages": []},
            models=["model1"],
            stream=True,
        )
        
        chunks = []
        async for chunk in result:
            chunks.append(chunk)
        
        assert len(chunks) == 1
        assert chunks[0] == b"data: chunk\n\n"

    @pytest.mark.asyncio
    async def test_execute_with_failover_skip_provider_error(self):
        """ProviderError は次のモデルへスキップする"""
        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        cloud_adapter.chat_completion = AsyncMock(
            side_effect=[
                ProviderError("error"),
                {"result": "success"},
            ]
        )
        local_adapter = MagicMock(spec=AbstractLLMAdapter)
        
        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
        )
        
        result = await router.execute_with_failover(
            payload={"messages": []},
            models=["model1", "model2"],
            stream=False,
        )
        
        assert result == {"result": "success"}
        assert cloud_adapter.chat_completion.call_count == 2
