import json
import logging
from typing import AsyncGenerator

import httpx

from .base import AbstractLLMAdapter, ProviderError, ProviderTimeoutError, RateLimitError, NotFoundError

logger = logging.getLogger(__name__)

# 利用可能モデルのフィルタ条件
_MIN_CONTEXT_WINDOW = 120000
_MIN_MAX_COMPLETION_TOKENS = 30000


class GroqAdapter(AbstractLLMAdapter):
    """Groq API アダプター（OpenAI 互換）"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.groq.com/openai/v1",
        min_context_window: int = _MIN_CONTEXT_WINDOW,
        min_max_completion_tokens: int = _MIN_MAX_COMPLETION_TOKENS
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._min_context_window = min_context_window
        self._min_max_completion_tokens = min_max_completion_tokens
        self._available_models: list[str] = []

    @property
    def provider_name(self) -> str:
        return "groq"

    @classmethod
    def from_config(cls, config: dict, api_key: str | None):
        """config から GroqAdapter を生成"""
        if not api_key:
            logger.warning("GROQ_API_KEY not set")
            return None
        return cls(
            api_key=api_key,
            base_url=config.get("base_url", "https://api.groq.com/openai/v1"),
            min_context_window=config.get("min_context_window", _MIN_CONTEXT_WINDOW),
            min_max_completion_tokens=config.get("min_max_completion_tokens", _MIN_MAX_COMPLETION_TOKENS),
        )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def list_models(self) -> list[str]:
        """Groq で利用可能なモデル一覧を取得"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._base_url}/models",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning(f"Failed to fetch Groq models: {exc}")
            return []

        all_models = data.get("data", [])
        usable = [
            m for m in all_models
            if m.get("context_window", 0) >= self._min_context_window
            and m.get("max_completion_tokens", 0) >= self._min_max_completion_tokens
        ]
        self._available_models = [m["id"] for m in usable]
        logger.info(
            f"Groq models: {len(all_models)} total, "
            f"{len(self._available_models)} usable "
            f"(context_window>={self._min_context_window}, "
            f"max_completion_tokens>={self._min_max_completion_tokens})"
        )
        for m in usable:
            logger.info(f"  {m['id']} (ctx={m.get('context_window')}, max_out={m.get('max_completion_tokens')})")
        return self._available_models

    def _resolve_model(self, model: str) -> str:
        """OpenRouter モデルID を Groq モデルID に変換（動的リストから検索）"""
        # model が空の場合は利用可能モデルの最初のものを使用
        if not model and self._available_models:
            default = self._available_models[0]
            logger.info(f"Groq using default model: {default}")
            return default
        
        bare = model.split("/")[-1].replace(":free", "").lower()
        for available in self._available_models:
            avail_bare = available.split("/")[-1].lower()
            if bare in avail_bare or avail_bare in bare:
                logger.debug(f"Groq model resolved: {model} → {available}")
                return available
        logger.debug(f"Groq model unresolved, passing through: {model}")
        return model

    def _filter_tool_calls_from_chunk(self, chunk: bytes) -> bytes:
        """SSE チャンクから tool_calls を除去"""
        try:
            lines = chunk.decode('utf-8').split('\n')
            filtered_lines = []
            for line in lines:
                if line.startswith('data: ') and line != 'data: [DONE]':
                    try:
                        data = json.loads(line[6:])  # "data: " を除去
                        if "choices" in data:
                            for choice in data["choices"]:
                                if "delta" in choice:
                                    choice["delta"].pop("tool_calls", None)
                        filtered_lines.append(f"data: {json.dumps(data)}")
                    except json.JSONDecodeError:
                        filtered_lines.append(line)
                else:
                    filtered_lines.append(line)
            return '\n'.join(filtered_lines).encode('utf-8')
        except Exception:
            # エラー時は元のチャンクをそのまま返す
            return chunk

    def _normalize_response(self, response: dict, remove_tool_calls: bool = False) -> None:
        """Groq 固有のフィールドを除去（OpenAI 互換形式に正規化）"""
        if "choices" in response:
            for choice in response["choices"]:
                if "message" in choice:
                    # reasoning フィールドを除去
                    choice["message"].pop("reasoning", None)
                    # tool_choice が none の場合は tool_calls も除去
                    if remove_tool_calls:
                        choice["message"].pop("tool_calls", None)
        # Groq 固有のメタデータフィールドを除去
        response.pop("usage_breakdown", None)
        response.pop("x_groq", None)
        response.pop("service_tier", None)

    async def chat_completion(self, payload: dict, model: str, timeout: float) -> dict:
        # model が空の場合、全利用可能モデルを順に試行
        models_to_try = [model] if model else self._available_models
        last_error = None
        
        for m in models_to_try:
            resolved = self._resolve_model(m)
            body = {**payload, "model": resolved, "stream": False}

            # tool_choice の処理
            remove_tool_calls = False
            if body.get("tool_choice") == "none":
                # "none" の場合は tools も除去
                body.pop("tools", None)
                body.pop("tool_choice", None)
                remove_tool_calls = True
            elif "tools" in body:
                # tools が存在する場合は tool_choice を "auto" に設定
                body["tool_choice"] = "auto"
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{self._base_url}/chat/completions",
                        headers=self._headers(),
                        json=body,
                    )
            except httpx.TimeoutException as exc:
                last_error = ProviderTimeoutError(f"Groq timeout ({resolved})")
                continue

            if resp.status_code == 429:
                last_error = RateLimitError(f"Groq 429 ({resolved})")
                continue
            if resp.status_code == 413:
                logger.warning(f"Groq 413 Payload Too Large ({resolved}), trying next model")
                last_error = ProviderError(f"Groq 413 ({resolved}): {resp.text[:200]}")
                continue
            if resp.status_code == 400:
                # tool calling 非対応モデルの場合、NotFoundError で 600s クールダウン
                error_text = resp.text
                if "tool" in error_text.lower():
                    logger.warning(f"Groq 400 tool calling error ({resolved}), marking as not found")
                    last_error = NotFoundError(f"Groq tool calling not supported ({resolved})")
                    continue
                last_error = ProviderError(f"Groq 400 ({resolved}): {resp.text[:200]}")
                continue
            if not resp.is_success:
                last_error = ProviderError(f"Groq {resp.status_code} ({resolved}): {resp.text[:200]}")
                continue

            # Groq 固有のフィールドを除去
            response_data = resp.json()
            self._normalize_response(response_data, remove_tool_calls=remove_tool_calls)
            return response_data

        # 全モデル失敗
        if last_error:
            raise last_error
        raise ProviderError("Groq: No models available")

    async def chat_completion_stream(
        self, payload: dict, model: str, timeout: float
    ) -> AsyncGenerator[bytes, None]:
        # model が空の場合、全利用可能モデルを順に試行
        models_to_try = [model] if model else self._available_models
        last_error = None
        
        for m in models_to_try:
            resolved = self._resolve_model(m)
            body = {**payload, "model": resolved, "stream": True}

            # tool_choice の処理
            remove_tool_calls = False
            if body.get("tool_choice") == "none":
                # "none" の場合は tools も除去
                body.pop("tools", None)
                body.pop("tool_choice", None)
                remove_tool_calls = True
            elif "tools" in body:
                # tools が存在する場合は tool_choice を "auto" に設定
                body["tool_choice"] = "auto"
            client = httpx.AsyncClient(timeout=timeout)
            try:
                req = client.build_request(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
                resp = await client.send(req, stream=True)

                if resp.status_code == 429:
                    await resp.aclose()
                    await client.aclose()
                    last_error = RateLimitError(f"Groq 429 ({resolved})")
                    continue
                if resp.status_code == 413:
                    await resp.aclose()
                    await client.aclose()
                    logger.warning(f"Groq 413 Payload Too Large ({resolved}), trying next model")
                    last_error = ProviderError(f"Groq 413 ({resolved})")
                    continue
                if resp.status_code == 400:
                    err = await resp.aread()
                    await client.aclose()
                    error_text = err.decode('utf-8', errors='replace')
                    # tool calling 非対応モデルの場合、NotFoundError で 600s クールダウン
                    if "tool" in error_text.lower():
                        logger.warning(f"Groq 400 tool calling error ({resolved}), marking as not found")
                        last_error = NotFoundError(f"Groq tool calling not supported ({resolved})")
                        continue
                    last_error = ProviderError(f"Groq 400 ({resolved}): {error_text[:200]}")
                    continue
                if not resp.is_success:
                    err = await resp.aread()
                    await client.aclose()
                    last_error = ProviderError(f"Groq {resp.status_code} ({resolved}): {err[:200]}")
                    continue

                # 成功: レスポンスを転送（tool_calls 除去が必要な場合はフィルタリング）
                try:
                    async for chunk in resp.aiter_bytes():
                        if chunk and remove_tool_calls:
                            # SSE チャンクから tool_calls を除去
                            chunk = self._filter_tool_calls_from_chunk(chunk)
                        if chunk:
                            yield chunk
                    return
                finally:
                    await client.aclose()

            except httpx.TimeoutException as exc:
                await client.aclose()
                last_error = ProviderTimeoutError(f"Groq timeout ({resolved})")
                continue

        # 全モデル失敗
        if last_error:
            raise last_error
        raise ProviderError("Groq: No models available")
