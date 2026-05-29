import logging
from typing import Any

import httpx
from langdetect import LangDetectException, detect

logger = logging.getLogger(__name__)

_LANG_CODE_TO_NAME: dict[str, str] = {
    "af": "Afrikaans",
    "ar": "Arabic",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "es": "Spanish",
    "et": "Estonian",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "gu": "Gujarati",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "kn": "Kannada",
    "ko": "Korean",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mk": "Macedonian",
    "ml": "Malayalam",
    "mr": "Marathi",
    "ne": "Nepali",
    "nl": "Dutch",
    "no": "Norwegian",
    "pa": "Punjabi",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "so": "Somali",
    "sq": "Albanian",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tl": "Filipino",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "zh-cn": "Chinese",
    "zh-tw": "Chinese",
}

_TRANSLATE_SYSTEM_PROMPT = (
    "You are a pure linguistic translator. "
    "Your ONLY task is to translate the user input text into English. "
    "CRITICAL RULES:\n"
    "- Output ONLY the translated English text. Nothing else.\n"
    "- DO NOT act as a code agent.\n"
    "- DO NOT generate file paths, file names, or arguments.\n"
    "- DO NOT use any tools or functions.\n"
    "- DO NOT add explanations, comments, or metadata.\n"
    "- If the input contains JSON keys, code markers, or special tags, preserve them exactly as-is.\n"
    "- If the input is already in English, return it unchanged.\n"
    "- If you cannot translate, return the input text as-is."
)


def _detect_language(messages: list[dict]) -> str | None:
    """userロールのメッセージのみで言語を検出する。

    systemメッセージはツール指示等の英語が含まれるため除外する。

    Returns:
        言語コード（例: "ja"）。検出失敗または英語の場合は None。
    """
    texts: list[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content.strip())
    combined = " ".join(texts)
    if not combined:
        return None
    try:
        lang = detect(combined)
    except LangDetectException:
        return None
    if lang == "en":
        return None
    return lang


def _should_translate(msg: dict) -> bool:
    """翻訳対象メッセージかどうかを判定する。

    systemメッセージはツール指示等の英語テンプレートが含まれるため翻訳しない。
    """
    if msg.get("role") not in ("user", "assistant"):
        return False
    if not isinstance(msg.get("content"), str):
        return False
    if msg.get("tool_calls"):
        return False
    return True


class TranslationPreprocessor:
    """英語以外のメッセージをOllamaで英語に翻訳するプリプロセッサ。"""

    def __init__(self, base_url: str, model: str, timeout: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    async def is_available(self) -> bool:
        """OllamaサーバーのHTTP疎通を確認する。"""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                return resp.is_success
        except Exception:
            return False

    async def _translate_text(self, text: str) -> str:
        """Ollamaを使ってテキストを英語に翻訳する。"""
        logger.debug(f"TRANSLATE input: {text!r}")
        body: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "temperature": 0.0,
            "max_tokens": 1024,
            "messages": [
                {"role": "system", "content": _TRANSLATE_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json=body,
            )
        resp.raise_for_status()
        data = resp.json()
        result = data["choices"][0]["message"]["content"].strip()
        logger.debug(f"TRANSLATE output: {result!r}")
        return result

    async def preprocess(self, payload: dict) -> dict:
        """ペイロードのメッセージを検査し、英語以外なら翻訳と言語指示を付与する。

        Returns:
            処理済みのペイロード（英語の場合は元のペイロードをそのまま返す）。
        """
        messages: list[dict] = payload.get("messages", [])
        lang_code = _detect_language(messages)

        if lang_code is None:
            return payload

        lang_name = _LANG_CODE_TO_NAME.get(lang_code, lang_code)
        logger.info(f"PREPROCESS detected={lang_code} ({lang_name}), translating messages")

        translated_messages: list[dict] = []
        system_injected = False

        for msg in messages:
            new_msg = dict(msg)
            if _should_translate(msg):
                original = msg["content"]
                translated = await self._translate_text(original)
                new_msg["content"] = translated

            if new_msg.get("role") == "system" and not system_injected:
                new_msg["content"] = (
                    new_msg["content"] + f"\n\nAlways respond in {lang_name}."
                )
                system_injected = True

            translated_messages.append(new_msg)

        if not system_injected:
            translated_messages.insert(
                0,
                {"role": "system", "content": f"Always respond in {lang_name}."},
            )

        logger.info(f"PREPROCESS done: {len(translated_messages)} messages")
        return {**payload, "messages": translated_messages}
