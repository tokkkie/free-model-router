import json
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ModelRouter:
    """OpenRouter の無料モデルリストを取得し、優先順位付けを行う"""

    def __init__(
        self,
        openrouter_base_url: str,
        priority_keywords: list[dict[str, Any]],
        exclude_keywords: list[str] | None = None,
        cache_ttl: int = 300,
    ) -> None:
        self._base_url = openrouter_base_url.rstrip("/")
        self._priority_keywords = priority_keywords
        self._exclude_keywords = exclude_keywords or []
        self._cache_ttl = cache_ttl
        self._cached_models: list[str] = []
        self._cache_time: float = 0.0
        self._known_vendors_file = "known_vendors.json"

    async def get_free_models(self) -> list[str]:
        """無料モデルリストを取得（キャッシュ付き）"""
        now = time.time()
        if self._cached_models and (now - self._cache_time) < self._cache_ttl:
            return self._cached_models

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self._base_url}/models")
            resp.raise_for_status()
            data = resp.json()

        all_models = data.get("data", [])

        free_models = [
            m["id"]
            for m in all_models
            if ":free" in m["id"]
            and m.get("pricing", {}).get("prompt") == "0"
            and m.get("pricing", {}).get("completion") == "0"
        ]

        self._detect_new_vendors(all_models, free_models)
        filtered_models = self._filter_excluded(free_models)
        sorted_models = self._sort_by_priority(filtered_models)
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

    def _filter_excluded(self, models: list[str]) -> list[str]:
        """除外キーワードに該当するモデルをフィルタリング"""
        if not self._exclude_keywords:
            return models

        filtered = []
        for model_id in models:
            is_excluded = any(
                keyword.lower() in model_id.lower()
                for keyword in self._exclude_keywords
            )
            if not is_excluded:
                filtered.append(model_id)

        return filtered

    def _detect_new_vendors(self, all_models: list[dict], free_models: list[str]) -> None:
        """新しいベンダーを検出し、Freeモデルがある場合は警告"""
        current_vendors = set(
            m["id"].split("/")[0] for m in all_models if "/" in m["id"]
        )

        known_vendors = self._load_known_vendors()

        if not known_vendors:
            self._save_known_vendors(current_vendors)
            return

        new_vendors = current_vendors - known_vendors

        if new_vendors:
            free_vendor_models = {}
            for model_id in free_models:
                if "/" in model_id:
                    vendor = model_id.split("/")[0]
                    if vendor in new_vendors:
                        if vendor not in free_vendor_models:
                            free_vendor_models[vendor] = []
                        free_vendor_models[vendor].append(model_id)

            if free_vendor_models:
                logger.warning(
                    "\n" + "="*70 + "\n"
                    "新しいベンダーのFreeモデルが検出されました。\n"
                    "ブラックリスト(exclude_keywords)への追加を検討してください:\n"
                )
                for vendor in sorted(free_vendor_models.keys()):
                    logger.warning(f"  [{vendor}]")
                    for model in free_vendor_models[vendor]:
                        logger.warning(f"    - {model}")
                logger.warning("="*70)

        self._save_known_vendors(current_vendors)

    def _load_known_vendors(self) -> set[str]:
        """既知のベンダーリストをファイルから読み込み"""
        if not os.path.exists(self._known_vendors_file):
            return set()

        try:
            with open(self._known_vendors_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("vendors", []))
        except Exception as e:
            logger.error(f"Failed to load known vendors: {e}")
            return set()

    def _save_known_vendors(self, vendors: set[str]) -> None:
        """ベンダーリストをファイルに保存"""
        try:
            with open(self._known_vendors_file, "w", encoding="utf-8") as f:
                json.dump({"vendors": sorted(vendors)}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save known vendors: {e}")
