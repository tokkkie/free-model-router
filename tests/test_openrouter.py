import sys
import importlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# モジュールを強制的に再読み込み（他のテストでのモックの影響を排除）
for mod in list(sys.modules.keys()):
    if mod.startswith('adapters.') or mod.startswith('router.'):
        del sys.modules[mod]

from adapters.base import ProviderError, ProviderTimeoutError, RateLimitError
from adapters.openrouter import OpenRouterAdapter


class TestOpenRouterAdapter:
    """OpenRouterAdapter のテスト"""

    def test_initialization(self):
        """初期化が正しく行われる"""
        adapter = OpenRouterAdapter(api_key="test-key", base_url="https://example.com/")
        assert adapter._api_key == "test-key"
        assert adapter._base_url == "https://example.com"

    def test_headers(self):
        """ヘッダーが正しく生成される"""
        adapter = OpenRouterAdapter(api_key="test-key")
        headers = adapter._headers()
        assert headers["Authorization"] == "Bearer test-key"
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_chat_completion_success(self):
        """正常なレスポンスが返される"""
        adapter = OpenRouterAdapter(api_key="test-key")
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_success = True
        mock_response.json.return_value = {"result": "success"}
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            
            result = await adapter.chat_completion(
                {"messages": [{"role": "user", "content": "test"}]},
                "test-model",
                10.0
            )
            
            assert result == {"result": "success"}

    @pytest.mark.asyncio
    async def test_chat_completion_timeout(self):
        """タイムアウト時に ProviderTimeoutError が raise される"""
        adapter = OpenRouterAdapter(api_key="test-key")
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("Timeout")
            )
            mock_client.return_value.__aenter__.return_value.post.side_effect = (
                __import__("httpx").TimeoutException("timeout")
            )
            
            with pytest.raises(ProviderTimeoutError, match="timeout"):
                await adapter.chat_completion({}, "test-model", 10.0)

    @pytest.mark.asyncio
    async def test_chat_completion_rate_limit(self):
        """429 エラー時に RateLimitError が raise される"""
        adapter = OpenRouterAdapter(api_key="test-key")
        
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.is_success = False
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            
            with pytest.raises(RateLimitError, match="429"):
                await adapter.chat_completion({}, "test-model", 10.0)

    @pytest.mark.asyncio
    async def test_chat_completion_error(self):
        """その他のエラー時に ProviderError が raise される"""
        adapter = OpenRouterAdapter(api_key="test-key")
        
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.is_success = False
        mock_response.text = "Internal Server Error"
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            
            with pytest.raises(ProviderError, match="500"):
                await adapter.chat_completion({}, "test-model", 10.0)

    @pytest.mark.asyncio
    async def test_chat_completion_stream_success(self):
        """ストリーミングが正しく動作する"""
        adapter = OpenRouterAdapter(api_key="test-key")
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_success = True
        
        async def mock_aiter_lines():
            yield 'data: {"choices": [{"delta": {"content": "test"}}]}'
            yield "data: [DONE]"
        
        mock_response.aiter_lines = mock_aiter_lines
        
        mock_client_instance = MagicMock()
        mock_client_instance.build_request.return_value = MagicMock()
        mock_client_instance.send = AsyncMock(return_value=mock_response)
        mock_client_instance.aclose = AsyncMock()
        
        with patch("httpx.AsyncClient", return_value=mock_client_instance):
            chunks = []
            async for chunk in adapter.chat_completion_stream({}, "test-model", 10.0):
                chunks.append(chunk)
            
            assert len(chunks) == 2
            assert b"data: " in chunks[0]
            assert chunks[1] == b"data: [DONE]\n\n"

    @pytest.mark.asyncio
    async def test_chat_completion_stream_rate_limit(self):
        """ストリーミング時の 429 エラーで RateLimitError が raise される"""
        adapter = OpenRouterAdapter(api_key="test-key")
        
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.is_success = False
        mock_response.aclose = AsyncMock()
        
        mock_client_instance = MagicMock()
        mock_client_instance.build_request.return_value = MagicMock()
        mock_client_instance.send = AsyncMock(return_value=mock_response)
        mock_client_instance.aclose = AsyncMock()
        
        with patch("httpx.AsyncClient", return_value=mock_client_instance):
            with pytest.raises(RateLimitError, match="429"):
                async for _ in adapter.chat_completion_stream({}, "test-model", 10.0):
                    pass
