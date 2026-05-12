"""モデルのツール呼び出し（function calling）サポートを検証する。

Roo Code など function calling を利用するクライアント向けに、
モデルが正しく tool_calls を返せるかを簡易的に確認する。
"""
import copy
import logging

from adapters.base import (
    AbstractLLMAdapter,
    NotFoundError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

# OpenRouter がツール非対応モデルに返す明示的なエラーメッセージ。
# これを検出したら「判定保留」ではなく「非対応」として確定させる。
_NO_TOOL_SUPPORT_MARKERS = (
    "No endpoints found that support tool use",
)

# シンプルなツール呼び出しを誘発するテストペイロード。
# モデルが tool_calls を返せば「対応」と判定する。
_TEST_PAYLOAD: dict = {
    "messages": [
        {
            "role": "user",
            "content": (
                "What is the current weather in Tokyo? "
                "You must call the get_weather tool to answer."
            ),
        }
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather for a given city.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "City name",
                        }
                    },
                    "required": ["city"],
                },
            },
        }
    ],
    "tool_choice": "auto",
    "max_tokens": 128,
    "temperature": 0,
}


async def verify_tool_support(
    adapter: AbstractLLMAdapter, model: str, timeout: float
) -> bool | None:
    """モデルがツール呼び出しを返せるか検証する。

    Returns:
        True:  tool_calls を返した（対応）
        False: tool_calls を返さなかった（非対応）
        None:  検証自体が失敗（429/timeout 等）→判定保留、次回起動時に再試行
    """
    payload = copy.deepcopy(_TEST_PAYLOAD)
    try:
        response = await adapter.chat_completion(payload, model, timeout)
    except (RateLimitError, ProviderTimeoutError) as exc:
        logger.info(f"Tool support verify deferred for {model}: {exc}")
        return None
    except NotFoundError as exc:
        logger.info(f"Model not found, marking as unsupported: {model}")
        return False
    except ProviderError as exc:
        message = str(exc)
        if any(marker in message for marker in _NO_TOOL_SUPPORT_MARKERS):
            logger.info(
                f"Tool support explicitly unsupported by provider for {model}"
            )
            return False
        logger.info(f"Tool support verify deferred for {model}: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.info(f"Tool support verify deferred for {model}: {exc}")
        return None

    choices = response.get("choices") or []
    if not choices:
        return False
    message = choices[0].get("message") or {}
    tool_calls = message.get("tool_calls")
    return bool(tool_calls)
