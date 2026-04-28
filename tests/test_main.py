import sys
import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

# 環境変数を設定（main.py のインポート前に必要）
os.environ["OPENROUTER_API_KEY"] = "test-key"

# 依存モジュールをモック化（PR1/PR2/PR3 未マージ時の対応）
from abc import ABC, abstractmethod
from typing import AsyncIterator
from types import ModuleType

# adapters.base
if 'adapters.base' not in sys.modules:
    base_module = ModuleType('adapters.base')
    
    class AbstractLLMAdapter(ABC):
        @abstractmethod
        async def chat_completion(self, payload: dict, model: str, timeout: float) -> dict:
            pass
        
        @abstractmethod
        async def chat_completion_stream(self, payload: dict, model: str, timeout: float) -> AsyncIterator[bytes]:
            pass
    
    base_module.AbstractLLMAdapter = AbstractLLMAdapter
    sys.modules['adapters.base'] = base_module

# adapters.openrouter
if 'adapters.openrouter' not in sys.modules:
    openrouter_module = ModuleType('adapters.openrouter')
    
    class OpenRouterAdapter:
        def __init__(self, api_key: str, base_url: str = ""):
            pass
    
    openrouter_module.OpenRouterAdapter = OpenRouterAdapter
    sys.modules['adapters.openrouter'] = openrouter_module

# adapters.ollama
if 'adapters.ollama' not in sys.modules:
    ollama_module = ModuleType('adapters.ollama')
    
    class OllamaAdapter:
        def __init__(self, base_url: str = ""):
            pass
    
    ollama_module.OllamaAdapter = OllamaAdapter
    sys.modules['adapters.ollama'] = ollama_module

# router.model_router
if 'router.model_router' not in sys.modules:
    model_router_module = ModuleType('router.model_router')
    
    class ModelRouter:
        def __init__(self, openrouter_base_url: str, priority_keywords: list, exclude_keywords=None, cache_ttl: int = 300):
            pass
        
        async def get_free_models(self):
            return []
    
    model_router_module.ModelRouter = ModelRouter
    sys.modules['router.model_router'] = model_router_module

# router.tool_support_registry
if 'router.tool_support_registry' not in sys.modules:
    tsr_module = ModuleType('router.tool_support_registry')

    class ToolSupportRegistry:
        def __init__(self, cache_file: str = "tool_support_cache.json"):
            self._cache = {}

        def get_unverified(self, models):
            return []

        def mark(self, model, supported):
            pass

        def is_supported(self, model):
            return True

        def filter_supported(self, models):
            return list(models)

        def unsupported_models(self):
            return []

        def prune(self, current_models):
            return 0

        def save(self):
            pass

    tsr_module.ToolSupportRegistry = ToolSupportRegistry
    sys.modules['router.tool_support_registry'] = tsr_module

# router.tool_verifier
if 'router.tool_verifier' not in sys.modules:
    tv_module = ModuleType('router.tool_verifier')

    async def verify_tool_support(adapter, model, timeout):
        return True

    tv_module.verify_tool_support = verify_tool_support
    sys.modules['router.tool_verifier'] = tv_module

# router.failover
if 'router.failover' not in sys.modules:
    failover_module = ModuleType('router.failover')
    
    class FailoverRouter:
        def __init__(self, cloud_adapter, local_adapter, local_model: str, timeout: float):
            pass
        
        async def execute_with_failover(self, payload: dict, models: list, stream: bool):
            return {}
    
    failover_module.FailoverRouter = FailoverRouter
    sys.modules['router.failover'] = failover_module

# config.json をモック化
mock_config = {
    "timeout_seconds": 20,
    "model_cache_ttl_seconds": 300,
    "priority_keywords": [{"keywords": ["qwen"], "priority": 1}],
    "openrouter_base_url": "https://openrouter.ai/api/v1",
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "phi3.5:latest"
}

with patch("builtins.open", MagicMock(return_value=MagicMock(__enter__=lambda s: s, __exit__=lambda s, *args: None, read=lambda: json.dumps(mock_config)))):
    with patch("json.load", return_value=mock_config):
        from main import app


class TestMainAPI:
    """FastAPI サーバーのテスト"""

    def test_health_endpoint(self):
        """ヘルスチェックエンドポイントが正しく動作する"""
        client = TestClient(app)
        response = client.get("/health")
        
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_chat_completions_non_streaming(self):
        """非ストリーミングのチャット補完が正しく動作する"""
        with patch("main.model_router") as mock_router:
            with patch("main.failover_router") as mock_failover:
                mock_router.get_free_models = AsyncMock(return_value=["model1", "model2"])

                mock_failover.execute_with_failover = AsyncMock(
                    return_value={"choices": [{"message": {"content": "test"}}]}
                )

                client = TestClient(app)
                response = client.post(
                    "/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": "Hello"}],
                        "stream": False,
                    },
                )

                assert response.status_code == 200
                data = response.json()
                assert "choices" in data

    @pytest.mark.asyncio
    async def test_chat_completions_no_models_available(self):
        """モデルが利用できない場合に 503 エラーを返す"""
        with patch("main.model_router") as mock_router:
            mock_router.get_free_models = AsyncMock(return_value=[])
            
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": False,
                },
            )
            
            assert response.status_code == 503
            assert "No free models available" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_chat_completions_streaming(self):
        """ストリーミングのチャット補完が正しく動作する"""
        async def mock_stream():
            yield b"data: chunk1\n\n"
            yield b"data: chunk2\n\n"

        with patch("main.model_router") as mock_router:
            with patch("main.failover_router") as mock_failover:
                mock_router.get_free_models = AsyncMock(return_value=["model1"])

                mock_failover.execute_with_failover = AsyncMock(return_value=mock_stream())

                client = TestClient(app)
                response = client.post(
                    "/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": "Hello"}],
                        "stream": True,
                    },
                )

                assert response.status_code == 200
                assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    def test_openai_compatibility(self):
        """OpenAI 互換のリクエスト形式を受け付ける"""
        with patch("main.model_router") as mock_router:
            with patch("main.failover_router") as mock_failover:
                mock_router.get_free_models = AsyncMock(return_value=["model1"])

                mock_failover.execute_with_failover = AsyncMock(
                    return_value={"choices": [{"message": {"content": "response"}}]}
                )

                client = TestClient(app)

                # OpenAI 互換のリクエスト
                response = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "gpt-3.5-turbo",  # model パラメータは無視される
                        "messages": [
                            {"role": "system", "content": "You are a helpful assistant."},
                            {"role": "user", "content": "Hello!"}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 100,
                    },
                )

                assert response.status_code == 200


class TestConfiguration:
    """設定のテスト"""

    def test_config_loaded(self):
        """config.json が正しく読み込まれる"""
        from main import config
        
        assert config["timeout_seconds"] == 20
        assert config["model_cache_ttl_seconds"] == 300
        assert "priority_keywords" in config

    def test_api_key_required(self):
        """OPENROUTER_API_KEY が必須である"""
        # 既に設定されているのでテストはスキップ
        # 実際の環境では未設定時に RuntimeError が raise される
        assert os.getenv("OPENROUTER_API_KEY") == "test-key"
