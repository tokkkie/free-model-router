"""verify_tool_support のテスト"""
import sys
import pytest
from unittest.mock import AsyncMock

# モジュールを強制的に再読み込み
for mod in list(sys.modules.keys()):
    if mod.startswith("adapters.") or mod.startswith("router."):
        del sys.modules[mod]

from adapters.base import ProviderError, ProviderTimeoutError, RateLimitError
from router.tool_verifier import verify_tool_support


class _FakeAdapter:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.chat_completion = AsyncMock(side_effect=self._side_effect)
        self.chat_completion_stream = AsyncMock()

    async def _side_effect(self, payload, model, timeout):
        if self._exc is not None:
            raise self._exc
        return self._response


class TestVerifyToolSupport:
    @pytest.mark.asyncio
    async def test_returns_true_when_tool_calls_present(self):
        adapter = _FakeAdapter(
            response={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"city": "Tokyo"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )
        result = await verify_tool_support(adapter, "x/y:free", timeout=5.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_tool_calls(self):
        adapter = _FakeAdapter(
            response={
                "choices": [
                    {"message": {"role": "assistant", "content": "I would call..."}}
                ]
            }
        )
        result = await verify_tool_support(adapter, "x/y:free", timeout=5.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_empty_choices(self):
        adapter = _FakeAdapter(response={"choices": []})
        result = await verify_tool_support(adapter, "x/y:free", timeout=5.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_none_on_rate_limit(self):
        adapter = _FakeAdapter(exc=RateLimitError("429"))
        result = await verify_tool_support(adapter, "x/y:free", timeout=5.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        adapter = _FakeAdapter(exc=ProviderTimeoutError("timeout"))
        result = await verify_tool_support(adapter, "x/y:free", timeout=5.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_provider_error(self):
        adapter = _FakeAdapter(exc=ProviderError("500"))
        result = await verify_tool_support(adapter, "x/y:free", timeout=5.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_false_on_explicit_no_tool_support_error(self):
        """OpenRouter が 404 で 'No endpoints found that support tool use' を返した場合は非対応確定"""
        exc = ProviderError(
            'OpenRouter 404 (x/y:free): {"error":{"message":"No endpoints found '
            'that support tool use. Try disabling ..."}}'
        )
        adapter = _FakeAdapter(exc=exc)
        result = await verify_tool_support(adapter, "x/y:free", timeout=5.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_sends_tools_in_payload(self):
        captured = {}

        class _CaptureAdapter:
            async def chat_completion(self, payload, model, timeout):
                captured["payload"] = payload
                captured["model"] = model
                return {"choices": [{"message": {"tool_calls": [{"id": "1"}]}}]}

            async def chat_completion_stream(self, payload, model, timeout):
                yield b""

        await verify_tool_support(_CaptureAdapter(), "test:free", timeout=5.0)
        assert "tools" in captured["payload"]
        assert captured["payload"]["tools"][0]["function"]["name"] == "get_weather"
        assert captured["model"] == "test:free"
