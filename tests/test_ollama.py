import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# モジュールを強制的に再読み込み（他のテストでのモックの影響を排除）
for mod in list(sys.modules.keys()):
    if mod.startswith('adapters.') or mod.startswith('router.'):
        del sys.modules[mod]

from adapters.base import ProviderError, ProviderTimeoutError
from adapters.ollama import OllamaAdapter


class TestOllamaAdapter:
    """OllamaAdapter のテスト"""

    def test_initialization(self):
        """初期化が正しく行われる"""
        adapter = OllamaAdapter(base_url="http://localhost:11434/")
        assert adapter._base_url == "http://localhost:11434"

    def test_default_base_url(self):
        """デフォルトの base_url が設定される"""
        adapter = OllamaAdapter()
        assert adapter._base_url == "http://localhost:11434"

    def test_headers(self):
        """ヘッダーが正しく生成される"""
        adapter = OllamaAdapter()
        headers = adapter._headers()
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_chat_completion_success(self):
        """正常なレスポンスが返される"""
        adapter = OllamaAdapter()
        
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
                "phi3.5:latest",
                10.0
            )
            
            assert result == {"result": "success"}

    @pytest.mark.asyncio
    async def test_chat_completion_timeout(self):
        """タイムアウト時に ProviderTimeoutError が raise される"""
        adapter = OllamaAdapter()
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=__import__("httpx").TimeoutException("timeout")
            )
            
            with pytest.raises(ProviderTimeoutError, match="timeout"):
                await adapter.chat_completion({}, "test-model", 10.0)

    @pytest.mark.asyncio
    async def test_chat_completion_connection_error(self):
        """接続エラー時に ProviderError が raise される"""
        adapter = OllamaAdapter()
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=__import__("httpx").ConnectError("connection failed")
            )
            
            with pytest.raises(ProviderError, match="connection failed"):
                await adapter.chat_completion({}, "test-model", 10.0)

    @pytest.mark.asyncio
    async def test_chat_completion_error(self):
        """その他のエラー時に ProviderError が raise される"""
        adapter = OllamaAdapter()
        
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
        adapter = OllamaAdapter()
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_success = True
        
        async def mock_aiter_bytes():
            yield b"data: chunk1\n\n"
            yield b"data: chunk2\n\n"
        
        mock_response.aiter_bytes = mock_aiter_bytes
        
        mock_client_instance = MagicMock()
        mock_client_instance.build_request.return_value = MagicMock()
        mock_client_instance.send = AsyncMock(return_value=mock_response)
        mock_client_instance.aclose = AsyncMock()
        
        with patch("httpx.AsyncClient", return_value=mock_client_instance):
            chunks = []
            async for chunk in adapter.chat_completion_stream({}, "test-model", 10.0):
                chunks.append(chunk)
            
            assert len(chunks) == 2
            assert chunks[0] == b"data: chunk1\n\n"
            assert chunks[1] == b"data: chunk2\n\n"

    @pytest.mark.asyncio
    async def test_chat_completion_stream_connection_error(self):
        """ストリーミング時の接続エラーで ProviderError が raise される"""
        adapter = OllamaAdapter()
        
        mock_client_instance = MagicMock()
        mock_client_instance.build_request.return_value = MagicMock()
        mock_client_instance.send = AsyncMock(
            side_effect=__import__("httpx").ConnectError("connection failed")
        )
        mock_client_instance.aclose = AsyncMock()
        
        with patch("httpx.AsyncClient", return_value=mock_client_instance):
            with pytest.raises(ProviderError, match="connection failed"):
                async for _ in adapter.chat_completion_stream({}, "test-model", 10.0):
                    pass
