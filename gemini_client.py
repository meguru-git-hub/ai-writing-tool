"""Gemini API の呼び出しをまとめたモジュール。"""

from collections.abc import Iterator
from functools import lru_cache

from google import genai
from google.genai import errors, types


class GeminiError(Exception):
    """画面にそのまま表示できるメッセージを持つエラー。"""


@lru_cache(maxsize=4)
def _client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def _friendly_message(e: errors.APIError) -> str:
    code = getattr(e, "code", None)
    detail = getattr(e, "message", None) or str(e)
    if code in (400, 401, 403) and "API key" in detail:
        return "APIキーが無効です。サイドバーの設定、または .env の GEMINI_API_KEY を確認してください。"
    if code == 404:
        return f"モデルが見つかりません。サイドバーでモデル名を確認してください。（{detail}）"
    if code == 429:
        return "APIの利用上限に達しました。しばらく待ってから再度お試しください。"
    if code in (500, 503):
        return "Gemini 側が混み合っているようです。少し待ってから再度お試しください。"
    return f"Gemini API でエラーが発生しました（{code}）: {detail}"


def _uses_custom_temperature(model: str) -> bool:
    """ツールごとの temperature を送るかどうか。

    Gemini 3 以降は temperature を標準値（1.0）のまま使うよう公式に推奨されている
    （下げると同じ文を繰り返すなど品質が落ちることがある）ため、2.x 以前のモデルだけ送る。
    """
    name = model.removeprefix("models/")
    return name.startswith(("gemini-1", "gemini-2"))


def stream_generate(
    api_key: str,
    model: str,
    system: str,
    messages: list[dict],
    temperature: float,
) -> Iterator[str]:
    """会話履歴を送り、生成されたテキストを少しずつ返す。

    messages は {"role": "user" | "model", "text": str} のリスト。
    """
    contents = [
        types.Content(role=m["role"], parts=[types.Part(text=m["text"])])
        for m in messages
    ]
    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature if _uses_custom_temperature(model) else None,
    )
    try:
        for chunk in _client(api_key).models.generate_content_stream(
            model=model, contents=contents, config=config
        ):
            if chunk.text:
                yield chunk.text
    except errors.APIError as e:
        raise GeminiError(_friendly_message(e)) from e
