"""ToolSupportRegistry のテスト"""
import json
import os
import sys

# モジュールを強制的に再読み込み（他のテストでのモックの影響を排除）
for mod in list(sys.modules.keys()):
    if mod.startswith("adapters.") or mod.startswith("router."):
        del sys.modules[mod]

from router.tool_support_registry import ToolSupportRegistry


class TestToolSupportRegistry:
    def test_load_missing_file_returns_empty(self, tmp_path):
        cache_file = tmp_path / "missing.json"
        registry = ToolSupportRegistry(cache_file=str(cache_file))
        assert registry._cache == {}

    def test_mark_and_filter(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        registry = ToolSupportRegistry(cache_file=str(cache_file))
        registry.mark("good/model:free", True)
        registry.mark("bad/model:free", False)

        models = ["good/model:free", "bad/model:free", "unknown/model:free"]
        filtered = registry.filter_supported(models)

        # unknown は未検証なので楽観的に含まれる
        assert "good/model:free" in filtered
        assert "unknown/model:free" in filtered
        assert "bad/model:free" not in filtered

    def test_get_unverified(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        registry = ToolSupportRegistry(cache_file=str(cache_file))
        registry.mark("known/model:free", True)

        unverified = registry.get_unverified(["known/model:free", "new/model:free"])
        assert unverified == ["new/model:free"]

    def test_save_and_load_roundtrip(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        reg1 = ToolSupportRegistry(cache_file=str(cache_file))
        reg1.mark("a/b:free", True)
        reg1.mark("c/d:free", False)
        reg1.save()

        reg2 = ToolSupportRegistry(cache_file=str(cache_file))
        assert reg2.is_supported("a/b:free") is True
        assert reg2.is_supported("c/d:free") is False
        assert reg2.is_supported("unknown:free") is True  # 未検証 = 楽観的

    def test_unsupported_models(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        registry = ToolSupportRegistry(cache_file=str(cache_file))
        registry.mark("a:free", True)
        registry.mark("b:free", False)
        registry.mark("c:free", False)

        assert registry.unsupported_models() == ["b:free", "c:free"]

    def test_prune_removes_stale(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        registry = ToolSupportRegistry(cache_file=str(cache_file))
        registry.mark("alive:free", True)
        registry.mark("stale:free", False)

        pruned = registry.prune(["alive:free"])
        assert pruned == 1
        assert "stale:free" not in registry._cache
        assert "alive:free" in registry._cache

    def test_load_corrupt_file_returns_empty(self, tmp_path):
        cache_file = tmp_path / "corrupt.json"
        cache_file.write_text("not json", encoding="utf-8")
        registry = ToolSupportRegistry(cache_file=str(cache_file))
        assert registry._cache == {}

    def test_load_unexpected_format_returns_empty(self, tmp_path):
        cache_file = tmp_path / "wrong.json"
        cache_file.write_text(json.dumps(["list", "not", "dict"]), encoding="utf-8")
        registry = ToolSupportRegistry(cache_file=str(cache_file))
        assert registry._cache == {}
