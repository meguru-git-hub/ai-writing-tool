# ✍️ AI ライティングツール

Python + Streamlit + Gemini API で作った、個人用の AI ライティングツールです。

## 使えるツール

| ツール | できること |
| --- | --- |
| 📝 ブログ記事作成（SEO） | メインキーワードと検索意図から SEO に強い記事を執筆。構成案だけの作成、目次・FAQ・メタディスクリプション・URLスラッグの出力にも対応 |
| ✉️ メール返信作成 | 受信メールを貼り付けて、相手・トーンに合った返信案を複数作成 |
| 📋 文章の要約 | 3行まとめ／箇条書き／詳細要約など形式を選んで要約 |
| ✅ 校正・推敲 | 誤字脱字チェックからブラッシュアップまで。修正点の説明付き |
| 🎨 文体変換・リライト | 敬語⇔カジュアル、やさしい日本語などに書き換え |
| 🌐 翻訳 | 自然な訳・ビジネス向けなどスタイルを選んで翻訳 |
| 📱 SNS投稿作成 | X・Instagram など媒体に合わせた投稿文を複数案作成 |
| 💡 タイトル・キャッチコピー | 記事タイトル・メール件名・キャッチコピーを大量に提案 |
| 🧠 アイデア出し・構成案 | ネタ出し、記事構成、ブレインストーミング |
| 🗂️ メモ整理・議事録 | 走り書きメモを議事録・ToDo・日報などに整形 |
| ✨ フリー指示 | 自由な指示で文章作成 |

どのツールでも、生成後に「もっと短く」「よりフォーマルに」などの**追加指示で結果を調整**できます。
結果はコピー・Markdown ダウンロードができ、このセッション内の履歴がサイドバーに残ります。

## セットアップ

```powershell
# 1. 仮想環境を作ってライブラリをインストール
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. APIキーを設定（https://aistudio.google.com/apikey で取得）
copy .env.example .env
# .env を開いて GEMINI_API_KEY=... を書き換える

# 3. 起動
streamlit run app.py
```

`.env` を使わず、画面左の「⚙️ 設定」から APIキーを入力することもできます。

標準では、アプリはこのパソコンからしか開けません（`.streamlit/config.toml` で設定。同じ Wi-Fi の他人に APIキーを使われないため）。
スマホなど同じ Wi-Fi の別の端末から使いたいときだけ、自宅などの信頼できるネットワークで次のように起動してください。

```powershell
streamlit run app.py --server.address 0.0.0.0
```

## ファイル構成

```
app.py            画面（Streamlit）
tools.py          各ツールの入力項目とプロンプト
gemini_client.py  Gemini API の呼び出し（ストリーミング）
```

## ツールを追加するには

`tools.py` に `Tool` を1つ定義して、ファイル末尾の `TOOLS` リストに追加するだけです。
入力フォームは `fields` の定義から自動で作られます。

```python
def _my_tool(v: dict) -> str:
    return f"次の文章を〇〇してください。\n\n{v['text']}"

MY_TOOL = Tool(
    id="my_tool",
    name="マイツール",
    icon="🔖",
    description="説明文",
    fields=[Field("text", "文章", "textarea", required=True)],
    build_prompt=_my_tool,
)
```
