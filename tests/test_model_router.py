import sys
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# モジュールを強制的に再読み込み（他のテストでのモックの影響を排除）
for mod in list(sys.modules.keys()):
    if mod.startswith('adapters.') or mod.startswith('router.'):
        del sys.modules[mod]

from router.model_router import ModelRouter


class TestModelRouter:
    """ModelRouter のテスト"""

    def test_initialization(self):
        """初期化が正しく行われる"""
        router = ModelRouter(
            openrouter_base_url="https://openrouter.ai/api/v1",
            priority_keywords=[{"keywords": ["qwen"], "priority": 1}],
            cache_ttl=300,
        )
        assert router._base_url == "https://openrouter.ai/api/v1"
        assert router._cache_ttl == 300
        assert router._cached_models == []

    @pytest.mark.asyncio
    async def test_get_free_models_success(self):
        """無料モデルリストが正しく取得される"""
        router = ModelRouter(
            openrouter_base_url="https://openrouter.ai/api/v1",
            priority_keywords=[
                {"keywords": ["qwen"], "priority": 1},
                {"keywords": ["gemini"], "priority": 2},
            ],
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {
                    "id": "qwen/qwen-2.5:free",
                    "pricing": {"prompt": "0", "completion": "0"},
                },
                {
                    "id": "google/gemini-flash:free",
                    "pricing": {"prompt": "0", "completion": "0"},
                },
                {
                    "id": "paid-model",
                    "pricing": {"prompt": "0.001", "completion": "0.002"},
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            models = await router.get_free_models()

            assert len(models) == 2
            assert "qwen/qwen-2.5:free" in models
            assert "google/gemini-flash:free" in models
            assert "paid-model" not in models

    @pytest.mark.asyncio
    async def test_priority_sorting(self):
        """優先順位に基づいてソートされる"""
        router = ModelRouter(
            openrouter_base_url="https://openrouter.ai/api/v1",
            priority_keywords=[
                {"keywords": ["qwen"], "priority": 1},
                {"keywords": ["gemini"], "priority": 2},
                {"keywords": ["phi"], "priority": 3},
            ],
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "phi/phi-3:free", "pricing": {"prompt": "0", "completion": "0"}},
                {
                    "id": "qwen/qwen-2.5:free",
                    "pricing": {"prompt": "0", "completion": "0"},
                },
                {
                    "id": "google/gemini-flash:free",
                    "pricing": {"prompt": "0", "completion": "0"},
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            models = await router.get_free_models()

            assert models[0] == "qwen/qwen-2.5:free"
            assert models[1] == "google/gemini-flash:free"
            assert models[2] == "phi/phi-3:free"

    @pytest.mark.asyncio
    async def test_cache_mechanism(self):
        """キャッシュが正しく動作する"""
        router = ModelRouter(
            openrouter_base_url="https://openrouter.ai/api/v1",
            priority_keywords=[],
            cache_ttl=1,
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {
                    "id": "test-model:free",
                    "pricing": {"prompt": "0", "completion": "0"},
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_get = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__.return_value.get = mock_get

            models1 = await router.get_free_models()
            models2 = await router.get_free_models()

            assert models1 == models2
            assert mock_get.call_count == 1

            time.sleep(1.1)

            models3 = await router.get_free_models()
            assert models3 == models1
            assert mock_get.call_count == 2

    def test_sort_by_priority(self):
        """_sort_by_priority が正しく動作する"""
        router = ModelRouter(
            openrouter_base_url="https://openrouter.ai/api/v1",
            priority_keywords=[
                {"keywords": ["qwen"], "priority": 1},
                {"keywords": ["gemini"], "priority": 2},
            ],
        )

        models = [
            "unknown-model:free",
            "google/gemini-flash:free",
            "qwen/qwen-2.5:free",
        ]

        sorted_models = router._sort_by_priority(models)

        assert sorted_models[0] == "qwen/qwen-2.5:free"
        assert sorted_models[1] == "google/gemini-flash:free"
        assert sorted_models[2] == "unknown-model:free"

    @pytest.mark.asyncio
    async def test_empty_response(self):
        """空のレスポンスを正しく処理する"""
        router = ModelRouter(
            openrouter_base_url="https://openrouter.ai/api/v1",
            priority_keywords=[],
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": []}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            models = await router.get_free_models()
            assert models == []
