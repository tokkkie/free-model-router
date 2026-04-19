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


@pytest.fixture(autouse=True)
def _reset_cooldown():
    """各テスト前後でクラス変数のクールダウン状態をリセット"""
    FailoverRouter._cooldown_until.clear()
    yield
    FailoverRouter._cooldown_until.clear()


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


class TestRateLimitCooldown:
    """429 クールダウン機能のテスト"""

    @pytest.mark.asyncio
    async def test_rate_limited_model_registered_to_cooldown(self):
        """429 を受けたモデルは cooldown リストに登録される"""
        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        cloud_adapter.chat_completion = AsyncMock(
            side_effect=[RateLimitError("429"), {"result": "success"}]
        )
        local_adapter = MagicMock(spec=AbstractLLMAdapter)

        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
            cooldown_seconds=60.0,
        )

        await router.execute_with_failover(
            payload={"messages": []},
            models=["model1", "model2"],
            stream=False,
        )

        assert "model1" in FailoverRouter._cooldown_until
        assert "model2" not in FailoverRouter._cooldown_until

    @pytest.mark.asyncio
    async def test_cooldown_model_is_skipped(self):
        """cooldown 中のモデルはリクエスト対象から除外される"""
        import time as _time
        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        cloud_adapter.chat_completion = AsyncMock(return_value={"result": "success"})
        local_adapter = MagicMock(spec=AbstractLLMAdapter)

        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
            cooldown_seconds=60.0,
        )

        FailoverRouter._cooldown_until["model1"] = _time.monotonic() + 60.0

        result = await router.execute_with_failover(
            payload={"messages": []},
            models=["model1", "model2"],
            stream=False,
        )

        assert result == {"result": "success"}
        assert cloud_adapter.chat_completion.call_count == 1
        called_model = cloud_adapter.chat_completion.call_args_list[0][0][1]
        assert called_model == "model2"

    @pytest.mark.asyncio
    async def test_expired_cooldown_entry_is_retried(self):
        """cooldown 期限切れのモデルは再試行対象に戻る"""
        import time as _time
        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        cloud_adapter.chat_completion = AsyncMock(return_value={"result": "success"})
        local_adapter = MagicMock(spec=AbstractLLMAdapter)

        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
            cooldown_seconds=60.0,
        )

        FailoverRouter._cooldown_until["model1"] = _time.monotonic() - 1.0

        result = await router.execute_with_failover(
            payload={"messages": []},
            models=["model1"],
            stream=False,
        )

        assert result == {"result": "success"}
        assert cloud_adapter.chat_completion.call_count == 1
        assert "model1" not in FailoverRouter._cooldown_until

    @pytest.mark.asyncio
    async def test_all_models_cooldown_falls_back_to_local(self):
        """全モデルが cooldown 中の場合はローカルへフォールバック"""
        import time as _time
        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        cloud_adapter.chat_completion = AsyncMock()
        local_adapter = MagicMock(spec=AbstractLLMAdapter)
        local_adapter.chat_completion = AsyncMock(return_value={"result": "local"})

        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
            cooldown_seconds=60.0,
        )

        now = _time.monotonic()
        FailoverRouter._cooldown_until["model1"] = now + 60.0
        FailoverRouter._cooldown_until["model2"] = now + 60.0

        result = await router.execute_with_failover(
            payload={"messages": []},
            models=["model1", "model2"],
            stream=False,
        )

        assert result == {"result": "local"}
        assert cloud_adapter.chat_completion.call_count == 0
        assert local_adapter.chat_completion.call_count == 1

    @pytest.mark.asyncio
    async def test_cooldown_disabled_when_zero(self):
        """cooldown_seconds=0 のとき cooldown は登録されない"""
        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        cloud_adapter.chat_completion = AsyncMock(
            side_effect=[RateLimitError("429"), {"result": "success"}]
        )
        local_adapter = MagicMock(spec=AbstractLLMAdapter)

        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
            cooldown_seconds=0.0,
        )

        await router.execute_with_failover(
            payload={"messages": []},
            models=["model1", "model2"],
            stream=False,
        )

        assert FailoverRouter._cooldown_until == {}

    @pytest.mark.asyncio
    async def test_stream_rate_limited_model_registered_to_cooldown(self):
        """ストリーミング時も 429 で cooldown 登録される"""
        async def failing_stream():
            raise RateLimitError("429")
            yield  # pragma: no cover

        async def ok_stream():
            yield b"data: chunk\n\n"

        cloud_adapter = MagicMock(spec=AbstractLLMAdapter)
        cloud_adapter.chat_completion_stream = MagicMock(
            side_effect=[failing_stream(), ok_stream()]
        )
        local_adapter = MagicMock(spec=AbstractLLMAdapter)

        router = FailoverRouter(
            cloud_adapter=cloud_adapter,
            local_adapter=local_adapter,
            local_model="phi3.5:latest",
            timeout=20.0,
            cooldown_seconds=60.0,
        )

        result = await router.execute_with_failover(
            payload={"messages": []},
            models=["model1", "model2"],
            stream=True,
        )

        chunks = [chunk async for chunk in result]
        assert chunks == [b"data: chunk\n\n"]
        assert "model1" in FailoverRouter._cooldown_until
        assert "model2" not in FailoverRouter._cooldown_until
