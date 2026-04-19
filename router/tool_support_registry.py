"""モデルごとのツール呼び出し対応状況を永続化するレジストリ。

- 一度検証したモデルはキャッシュファイルに記録し、次回以降は再検証しない。
- 未検証モデルは「対応している」と楽観的に扱う（API エラーによる誤除外を避けるため）。
- 明示的に「非対応」と記録されたモデルのみフィルタで除外する。
"""
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


class ToolSupportRegistry:
    """モデルごとの tool calling サポート状況を管理する。"""

    def __init__(self, cache_file: str = "tool_support_cache.json") -> None:
        self._cache_file = cache_file
        self._cache: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not os.path.exists(self._cache_file):
            return {}
        try:
            with open(self._cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            logger.warning(
                f"Tool support cache has unexpected format, resetting: {self._cache_file}"
            )
            return {}
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to load tool support cache: {exc}")
            return {}

    def save(self) -> None:
        try:
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2, ensure_ascii=False, sort_keys=True)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to save tool support cache: {exc}")

    def get_unverified(self, models: list[str]) -> list[str]:
        """キャッシュに未記録のモデルのみを返す。"""
        return [m for m in models if m not in self._cache]

    def mark(self, model: str, supported: bool) -> None:
        self._cache[model] = {
            "tool_support": bool(supported),
            "verified_at": time.time(),
        }

    def is_supported(self, model: str) -> bool:
        """未検証は True（楽観的）。明示的に False と記録されている場合のみ False。"""
        entry = self._cache.get(model)
        if entry is None:
            return True
        return bool(entry.get("tool_support", True))

    def filter_supported(self, models: list[str]) -> list[str]:
        return [m for m in models if self.is_supported(m)]

    def unsupported_models(self) -> list[str]:
        return sorted(
            m for m, v in self._cache.items() if not v.get("tool_support", True)
        )

    def prune(self, current_models: list[str]) -> int:
        """モデルリストから消えたものをキャッシュから削除する。削除件数を返す。"""
        current_set = set(current_models)
        to_remove = [m for m in self._cache if m not in current_set]
        for m in to_remove:
            del self._cache[m]
        return len(to_remove)
