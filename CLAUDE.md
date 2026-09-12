# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概要

個人用の AI ライティングツール（ブログ記事・メール返信・要約・校正・翻訳など）。Python + Streamlit + Gemini API（`google-genai` SDK）で作っている。

## 作業ルール

- ユーザーへの返答・説明は日本語で行う。ユーザーは学習中なので、専門用語は短く補足する
- 画面の文言・プロンプト・コード内のコメントは日本語で書く
- 個人用のため、データベース・ログイン認証・ユーザー管理は追加しない。保存が必要なものはセッション内（`st.session_state`）かファイルのダウンロードで済ませる
- ライブラリは `requirements.txt` の3つ（streamlit / google-genai / python-dotenv）で足りるようにする。追加が必要な場合は、先にユーザーに理由を説明して確認する
- ツールを追加・変更したら、`README.md` の「使えるツール」の表も更新する
- 変更後は、下の「動作確認」の方法で全ツールがエラーなく動くことを確かめてから完了を報告する

## コマンド

Windows / PowerShell 環境。仮想環境は `.venv`。

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

APIキーは `.env` の `GEMINI_API_KEY` から読む（任意で `GEMINI_MODEL` も指定可。`.env.example` を参照）。サイドバーで入力したキーがあれば、そちらが優先される。

### 動作確認

テストファイルやリンターは用意していない。本物の API を呼ばずに画面を確認するには `streamlit.testing.v1.AppTest` を使う。

- `GEMINI_API_KEY` にダミー値を入れる
- `gemini_client.stream_generate` を偽のジェネレーターに差し替えてから `AppTest.from_file("app.py").run()` を呼ぶ。`app.py` は `from gemini_client import stream_generate` で読み込むため、差し替えは実行前に済ませる必要がある
- ウィジェットは下の「session_state の決まりごと」にあるキー名で操作する

使い捨てのテストスクリプトは、プロジェクトではなくスクラッチパッドに置く。

## 全体の仕組み

3つのファイルで構成し、ツールはデータとして定義する。

- **`tools.py`**：各ツールは `Tool` データクラスで、名前などの情報、入力項目（`Field` のリスト）、`build_prompt(values: dict) -> str`、システムプロンプト（標準は `BASE_SYSTEM`）、temperature を持つ。**ツールの追加は `Tool` を定義して `TOOLS` に足すだけで、`app.py` の変更は不要。** `build_prompt` に渡る `values` のキーは `Field.key`。使える `Field.kind` は `text` / `textarea` / `select` / `radio` / `multiselect` / `slider` / `checkbox`。新しい種類を増やすときは、`app.py` の `render_field()` と `default_value()` の両方に対応を追加する
- **`gemini_client.py`**：`stream_generate(api_key, model, system, messages, temperature)` が文章を少しずつ返す。`messages` は `{"role": "user" | "model", "text": str}` のリスト。`google.genai.errors.APIError` は、画面にそのまま表示できる日本語メッセージを持つ `GeminiError` に変換する
- **`app.py`**：サイドバー（ツール選択・設定・履歴）と、選んだツールの入力項目から作るフォームを表示する

### プロンプトの書き方（tools.py）

新しいツールも、今あるツールと同じ書き方に揃える。

- 条件は `_line(ラベル, 値)` で「- ラベル: 値」の行にする（値が空なら行を出さない）
- 各ツールの最後に `EXTRA_FIELD`（追加の指示）を置き、プロンプト側では `_extra(v)` を入れる
- ユーザーが貼り付けた長い文章は、プロンプトの**最後**にコードブロック（```）で囲んで入れる
- 要約・校正・議事録など元の文章を扱うツールには「原文にない情報を付け加えない」と明記する
- temperature の目安：正確さが大事なもの（要約・校正・翻訳・議事録）は 0.3、普通の文章は 0.6〜0.8、アイデアやキャッチコピーは 0.9〜1.0。ただしこの値が送られるのは Gemini 2.x 以前のモデルだけ（下の「Gemini のモデル」を参照）。Gemini 3 系では出力の傾向をプロンプトの指示で調整する
- 前置きなしで成果物だけを出力させる指示は `BASE_SYSTEM` に入っているので、各ツールで繰り返さない

### 生成の流れ（app.py）

生成を実行する場所と、生成を依頼するボタンを `st.session_state.pending[tool_id]` で切り離している。

1. メインのフォームを送信すると、`pending` に新しい会話 `[ユーザーのプロンプト]` が入る。調整ボタン・調整フォームは結果の下に表示されるため `on_click` コールバックを使い、`conversations[tool_id] + [追加の指示]` を入れる
2. `main()` で `pending` を取り出し、`run_generation()` が `st.empty()` の枠に（画像を無効にして）表示しながら生成する。生成後は `conversations[tool_id] = messages + [AIの返答]` として保存して `history` にも追加し、`st.rerun()` で画面を描き直す。結果は `render_result()` が1回だけ表示する

つまり「結果の調整」は会話の続きとして扱い、そのツールの会話をすべて毎回送り直している。

### session_state の決まりごと

- `conversations`（ツールごとの会話）、`pending`（ツールごとの生成待ち）、`history`（直近30件、セッション内のみ）
- フォームのウィジェットのキーは `w__{tool_id}__{field_key}`。`init_state()` が毎回 `w__*` のキーを再代入して、表示していないツールの入力値を Streamlit に消されないようにしている（ツールを切り替えても入力が残るのはこのため）
- そのため `render_field()` では `st.session_state.setdefault(key, ...)` で初期値を入れ、ウィジェットに `value=` や `index=` を渡してはいけない（渡すと Streamlit が「初期値と Session State の両方が指定された」という警告を出す）
- Streamlit 1.54 以上なので、非推奨の `use_container_width` ではなく `width="stretch"` を使う
- AI の出力を `st.markdown` で表示するときは、必ず `disable_images()` を通す（外部画像の URL を使った情報の持ち出しを防ぐため。`st.write_stream` も画像を表示するので使わない）

## Gemini のモデル

- 標準は `gemini-3.6-flash`。選択肢は `app.py` の `MODEL_OPTIONS` にある。`gemini-2.5-*` は新規ユーザーには提供が終わっており、API がエラーを返すので使わない
- モデル名を追加・変更するときは、推測で書かず、Google の公式ドキュメント（https://ai.google.dev/gemini-api/docs/models）で実在するモデル名か確認する
- Gemini 3 系は temperature を標準値（1.0）のまま使うよう公式に推奨されている（下げると同じ文を繰り返すなど品質が落ちることがある）。そのため `gemini_client._uses_custom_temperature()` で、2.x 以前のモデルにだけ `Tool.temperature` を送っている
- API は今のところ `generate_content_stream` を使っている。Google は新しい Interactions API を推奨しているが、まだ移行していない
