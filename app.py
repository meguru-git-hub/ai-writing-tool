"""AI ライティングツール（Streamlit + Gemini API）

起動: streamlit run app.py
"""

import os
import re
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from gemini_client import GeminiError, stream_generate
from tools import TOOL_MAP, TOOLS, Field, Tool

load_dotenv()

st.set_page_config(page_title="AI ライティングツール", page_icon="✍️", layout="wide")

MODEL_OPTIONS = ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview"]
CUSTOM_MODEL = "その他（手入力）"
QUICK_REFINES = ["もっと短く", "もっと詳しく", "よりフォーマルに", "よりカジュアルに", "別の案を出して"]
HISTORY_LIMIT = 30
WIDGET_PREFIX = "w__"
# Markdown の画像。3つの書き方すべてに対応する:
#   ![説明](URL) / ![説明][参照名] / ![説明]（URL は別の行の「[説明]: URL」で定義）
# 説明の中に [ ] が1組入っていても取りこぼさないようにしている。
IMAGE_MD = re.compile(r"!\[((?:[^\[\]]|\[[^\]]*\])*)\](\([^)]*\)|\[[^\]]*\])?")


def disable_images(text: str) -> str:
    """Markdown の画像を、読み込まれないただの文字に置き換える。

    貼り付けたメールなどに仕込まれた指示で AI が外部 URL の画像を出力すると、
    表示した瞬間に URL に含まれた文章が外部に送られてしまうため。
    """
    return IMAGE_MD.sub(lambda m: f"（画像: {m.group(1).strip() or '無題'}）", text)


def init_state() -> None:
    ss = st.session_state
    ss.setdefault("conversations", {})  # tool_id -> [{"role", "text"}, ...]
    ss.setdefault("pending", {})  # tool_id -> 生成待ちの会話
    ss.setdefault("history", [])  # 生成結果の履歴（このセッション内のみ）
    # Streamlit は表示されていないウィジェットの値を消すため、再代入して
    # ツールを切り替えても入力内容が残るようにする
    for key in list(ss.keys()):
        if isinstance(key, str) and key.startswith(WIDGET_PREFIX):
            ss[key] = ss[key]


def widget_key(tool: Tool, f: Field) -> str:
    return f"{WIDGET_PREFIX}{tool.id}__{f.key}"


def default_value(f: Field):
    if f.default is not None:
        return f.default
    return {
        "select": f.options[0] if f.options else None,
        "radio": f.options[0] if f.options else None,
        "slider": f.min_value,
        "checkbox": False,
        "multiselect": [],
    }.get(f.kind, "")


def render_field(tool: Tool, f: Field):
    key = widget_key(tool, f)
    st.session_state.setdefault(key, default_value(f))
    label = f"{f.label} *" if f.required else f.label
    common = {"key": key, "help": f.help}

    if f.kind == "text":
        return st.text_input(label, placeholder=f.placeholder, **common)
    if f.kind == "textarea":
        return st.text_area(label, placeholder=f.placeholder, height=f.height, **common)
    if f.kind == "select":
        return st.selectbox(label, f.options, **common)
    if f.kind == "radio":
        return st.radio(label, f.options, horizontal=True, **common)
    if f.kind == "multiselect":
        return st.multiselect(label, f.options, **common)
    if f.kind == "slider":
        return st.slider(label, f.min_value, f.max_value, step=f.step, **common)
    if f.kind == "checkbox":
        return st.checkbox(label, **common)
    raise ValueError(f"未対応のフィールド種別: {f.kind}")


# ---------------------------------------------------------------------------
# サイドバー
# ---------------------------------------------------------------------------
def render_sidebar() -> tuple[Tool, str, str]:
    env_key = os.getenv("GEMINI_API_KEY", "")
    env_model = os.getenv("GEMINI_MODEL", MODEL_OPTIONS[0])

    with st.sidebar:
        st.title("✍️ AI ライティング")
        tool_id = st.radio(
            "ツール",
            [t.id for t in TOOLS],
            format_func=lambda i: f"{TOOL_MAP[i].icon}  {TOOL_MAP[i].name}",
            label_visibility="collapsed",
            key="tool_id",
        )

        st.divider()
        with st.expander("⚙️ 設定", expanded=not env_key):
            input_key = st.text_input(
                "Gemini APIキー",
                type="password",
                placeholder="設定済み（.env）" if env_key else "AIza...",
                help="空欄の場合は .env の GEMINI_API_KEY を使います。"
                     "キーは https://aistudio.google.com/apikey で取得できます。",
            )
            options = MODEL_OPTIONS if env_model in MODEL_OPTIONS else [env_model, *MODEL_OPTIONS]
            model = st.selectbox("モデル", [*options, CUSTOM_MODEL])
            if model == CUSTOM_MODEL:
                model = st.text_input("モデル名", placeholder="例：gemini-3.6-flash").strip()
            st.caption("3.6-flash: 標準 / 3.8-flash: 最新で高性能 / flash-lite: 最速・低コスト / pro: 最高品質だが遅め（プレビュー版）")

        api_key = input_key.strip() or env_key
        if api_key:
            st.success("APIキー: 設定済み", icon="🔑")
        else:
            st.warning("APIキーが未設定です", icon="🔑")

        render_history()

    return TOOL_MAP[tool_id], api_key, model


def render_history() -> None:
    history = st.session_state.history
    if not history:
        return
    st.divider()
    st.subheader("📚 履歴")
    st.caption("ページを再読み込みすると消えます")
    for i, item in enumerate(reversed(history)):
        with st.expander(f"{item['time']} {item['icon']} {item['preview']}"):
            st.code(item["text"], language=None, wrap_lines=True)
            st.download_button(
                "ダウンロード", item["text"], file_name=f"{item['tool']}_{item['stamp']}.md",
                key=f"dl_hist_{len(history) - i}", width="stretch",
            )


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def queue_refine(tool_id: str, instruction: str) -> None:
    conv = st.session_state.conversations.get(tool_id)
    if conv and instruction.strip():
        st.session_state.pending[tool_id] = [*conv, {"role": "user", "text": instruction.strip()}]


def submit_refine_form(tool_id: str) -> None:
    key = f"refine__{tool_id}"
    queue_refine(tool_id, st.session_state.get(key, ""))
    st.session_state[key] = ""


def run_generation(tool: Tool, messages: list[dict], api_key: str, model: str) -> None:
    """ストリーミングで生成し、成功したら会話と履歴に保存する。"""
    st.subheader("生成中…")
    with st.container(border=True):
        placeholder = st.empty()
        text = ""
        try:
            # st.write_stream は画像もそのまま表示するため、画像を無効にしながら少しずつ表示する
            for chunk in stream_generate(api_key, model, tool.system, messages, tool.temperature):
                text += chunk
                placeholder.markdown(disable_images(text))
        except GeminiError as e:
            st.error(str(e), icon="🚨")
            return
        except Exception as e:  # 想定外のエラーも画面に出す
            st.error(f"予期しないエラーが発生しました: {e}", icon="🚨")
            return

    if not isinstance(text, str) or not text.strip():
        st.warning("結果が空でした。入力内容を変えて再度お試しください。")
        return

    st.session_state.conversations[tool.id] = [*messages, {"role": "model", "text": text}]
    now = datetime.now()
    first_line = text.strip().splitlines()[0].lstrip("#* ").strip()
    st.session_state.history.append({
        "tool": tool.id,
        "icon": tool.icon,
        "time": now.strftime("%H:%M"),
        "stamp": now.strftime("%Y%m%d_%H%M%S"),
        "preview": first_line[:20] + ("…" if len(first_line) > 20 else ""),
        "text": text,
    })
    del st.session_state.history[:-HISTORY_LIMIT]
    st.rerun()


def render_result(tool: Tool) -> None:
    conv = st.session_state.conversations.get(tool.id)
    if not conv:
        return
    output = conv[-1]["text"]
    turns = sum(1 for m in conv if m["role"] == "model")

    st.subheader("生成結果" + (f"（{turns}回目の調整）" if turns > 1 else ""))
    with st.container(border=True):
        st.markdown(disable_images(output))
    st.caption(f"{len(output):,} 文字")

    col1, col2 = st.columns(2)
    col1.download_button(
        "💾 ダウンロード（.md）", output,
        file_name=f"{tool.id}_{datetime.now():%Y%m%d_%H%M%S}.md",
        width="stretch",
    )
    if col2.button("🗑️ 結果をクリア", width="stretch"):
        st.session_state.conversations.pop(tool.id, None)
        st.rerun()

    with st.expander("📋 コピー用テキスト（右上のアイコンでコピー）"):
        st.code(output, language=None, wrap_lines=True)

    st.markdown("##### 🔧 結果を調整する")
    cols = st.columns(len(QUICK_REFINES))
    for col, label in zip(cols, QUICK_REFINES):
        col.button(label, key=f"quick__{tool.id}__{label}", width="stretch",
                   on_click=queue_refine, args=(tool.id, label))
    with st.form(f"refine_form__{tool.id}", border=False):
        c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
        c1.text_input(
            "追加の指示", key=f"refine__{tool.id}", label_visibility="collapsed",
            placeholder="例：2段落目をもっと具体的に / 結論を最初に持ってきて",
        )
        c2.form_submit_button("送信", width="stretch",
                              on_click=submit_refine_form, args=(tool.id,))


def main() -> None:
    init_state()
    tool, api_key, model = render_sidebar()

    st.title(f"{tool.icon} {tool.name}")
    st.caption(tool.description)

    with st.form(f"form__{tool.id}"):
        values = {f.key: render_field(tool, f) for f in tool.fields}
        submitted = st.form_submit_button("✨ 生成する", type="primary", width="stretch")

    if submitted:
        missing = [f.label for f in tool.fields
                   if f.required and not str(values.get(f.key, "")).strip()]
        if missing:
            st.error(f"次の項目を入力してください: {'、'.join(missing)}")
        else:
            st.session_state.pending[tool.id] = [{"role": "user", "text": tool.build_prompt(values)}]

    pending = st.session_state.pending.pop(tool.id, None)
    if pending:
        if not api_key:
            st.error("Gemini APIキーが設定されていません。サイドバーの「⚙️ 設定」から入力してください。")
        elif not model:
            st.error("モデル名を入力してください。")
        else:
            run_generation(tool, pending, api_key, model)

    render_result(tool)


main()
