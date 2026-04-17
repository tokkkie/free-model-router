import time
from typing import Any

import httpx


class ModelRouter:
    """OpenRouter の無料モデルリストを取得し、優先順位付けを行う"""

    def __init__(
        self,
        openrouter_base_url: str,
        priority_keywords: list[dict[str, Any]],
        cache_ttl: int = 300,
    ) -> None:
        self._base_url = openrouter_base_url.rstrip("/")
        self._priority_keywords = priority_keywords
        self._cache_ttl = cache_ttl
        self._cached_models: list[str] = []
        self._cache_time: float = 0.0

    async def get_free_models(self) -> list[str]:
        """無料モデルリストを取得（キャッシュ付き）"""
        now = time.time()
        if self._cached_models and (now - self._cache_time) < self._cache_ttl:
            return self._cached_models

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self._base_url}/models")
            resp.raise_for_status()
            data = resp.json()

        free_models = [
            m["id"]
            for m in data.get("data", [])
            if ":free" in m["id"]
            and m.get("pricing", {}).get("prompt") == "0"
            and m.get("pricing", {}).get("completion") == "0"
        ]

        sorted_models = self._sort_by_priority(free_models)
        self._cached_models = sorted_models
        self._cache_time = now
        return sorted_models

    def _sort_by_priority(self, models: list[str]) -> list[str]:
        """優先度キーワードに基づいてモデルをソート"""

        def priority_score(model_id: str) -> int:
            for rule in self._priority_keywords:
                for keyword in rule["keywords"]:
                    if keyword.lower() in model_id.lower():
                        return rule["priority"]
            return 999

        return sorted(models, key=priority_score)
