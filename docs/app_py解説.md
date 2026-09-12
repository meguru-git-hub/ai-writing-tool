# 【初心者向け】AIライティングツールの `app.py` を読み解く

AIライティングツールの画面を担当しているのが `app.py` です。257行ありますが、仕組みはそれほど複雑ではありません。

この記事では、**「ボタンを押してから結果が表示されるまでに何が起きているのか」** を順番に見ていきます。

## 目次

- [まず知っておきたいこと：Streamlit は毎回「上から全部やり直す」](#まず知っておきたいことstreamlit-は毎回上から全部やり直す)
- [全体の地図](#全体の地図)
- [① 準備（1〜23行目）](#-準備123行目)
- [② メモ帳を準備する：init_state()](#-メモ帳を準備するinit_state)
- [③ 入力欄を自動で作る：render_field()](#-入力欄を自動で作るrender_field)
- [④ サイドバー：render_sidebar()](#-サイドバーrender_sidebar)
- [⑤ 「生成する」ボタンが押されたら](#-生成するボタンが押されたら)
- [⑥ なぜ「予約」にするのか？](#-なぜ予約にするのか)
- [⑦ AI に書かせる：run_generation()](#-ai-に書かせるrun_generation)
- [⑧ 結果を表示する：render_result()](#-結果を表示するrender_result)
- [まとめ](#まとめ)

---

## まず知っておきたいこと：Streamlit は毎回「上から全部やり直す」

コードを読む前に、Streamlit のいちばん大事な特徴を押さえておきましょう。

> **Streamlit は、ボタンを押したり文字を入力したりするたびに、`app.py` を1行目から最後まで実行し直します。**

これを **「再実行（rerun）」** と呼びます。Streamlit を理解するうえでの一番のポイントです。

たとえるなら、**毎回ホワイトボードを全部消して、最初から書き直している** ようなものです。

そうなると、普通の変数は毎回リセットされてしまいます。生成した文章を変数に入れておいても、次にボタンを押した瞬間に消えてしまいます。

そこで使うのが **`st.session_state`** です。これは **再実行しても消えない「メモ帳」** で、覚えておきたいものは全部ここに書いておきます。

> 💡 **この2つが分かれば、`app.py` の9割は読めます。**
>
> 1. Streamlit は毎回、上から全部やり直す
> 2. 覚えておきたいものは `st.session_state` に入れる

---

## 全体の地図

プログラムは最後の行 `main()`（256行目）から動き出します。`main()` の中身は、ほぼ次の流れです。

```text
main()
 ├─ init_state()        … メモ帳（session_state）を準備する
 ├─ render_sidebar()    … 左のサイドバーを表示する
 ├─ タイトルと入力フォームを表示する
 ├─ 「生成する」が押されたら → 生成の予約を入れる
 ├─ 予約があれば → run_generation() で AI に文章を書かせる
 └─ render_result()     … 結果と「調整する」ボタンを表示する
```

これから、この順番に見ていきます。

---

## ① 準備（1〜23行目）

```python
from gemini_client import GeminiError, stream_generate
from tools import TOOL_MAP, TOOLS, Field, Tool

load_dotenv()
```

ほかのファイルから部品を読み込んでいます。

| 読み込んでいるもの | 役割 |
|---|---|
| `gemini_client.py` | Gemini と実際にやり取りする係 |
| `tools.py` | 「ブログ記事作成」「メール返信」など、各ツールの設計図 |
| `load_dotenv()` | `.env` ファイルに書いた APIキーを読み込む |

その下にある大文字の変数は **「定数」** で、アプリ全体で使う決まった値です。

```python
MODEL_OPTIONS = ["gemini-3.6-flash", ...]            # 選べるモデル
QUICK_REFINES = ["もっと短く", "もっと詳しく", ...]   # 調整ボタンの文言
HISTORY_LIMIT = 30                                   # 履歴は最大30件
```

> 💡 調整ボタンを増やしたいときは、`QUICK_REFINES` に言葉を足すだけです。定数にしておくと、こうした変更が楽になります。

---

## ② メモ帳を準備する：`init_state()`

📍 26行目〜

```python
ss.setdefault("conversations", {})  # ツールごとの会話
ss.setdefault("pending", {})        # 生成の予約
ss.setdefault("history", [])        # 履歴
```

`setdefault` は **「まだなければ作る。すでにあれば何もしない」** という命令です。毎回実行されても、中身が消えることはありません。

メモ帳には3つのページがあります。

| ページ | 書いてあること |
|---|---|
| `conversations` | ツールごとの、AIとのやり取り（最新の結果を含む） |
| `pending` | 「これから生成してね」という予約 |
| `history` | 過去に生成した文章（サイドバーの履歴） |

### ちょっと不思議な3行

```python
for key in list(ss.keys()):
    if isinstance(key, str) and key.startswith(WIDGET_PREFIX):
        ss[key] = ss[key]
```

`ss[key] = ss[key]` は、自分自身を代入しているだけで、一見意味がなさそうです。これは **Streamlit の仕様への対策** です。

Streamlit は、**画面に表示されていない入力欄の値を自動で捨ててしまいます。** そのため、「ブログ記事」から「メール返信」に切り替えると、ブログ記事で入力したテーマが消えてしまいます。

そこで、名前が `w__` で始まるもの（入力欄の値）を毎回「書き直す」ことで、**「これは大事なメモです」と Streamlit に伝えて、消されないようにしている** のです。

---

## ③ 入力欄を自動で作る：`render_field()`

📍 54行目〜

このアプリには11個のツールがありますが、**ツールごとの入力画面を1つずつ手で書いてはいません。**

`tools.py` には、ツールごとに次のような「設計図」が書いてあります。

```python
Field("topic", "テーマ", "text", required=True)
Field("tone", "文体・トーン", "select", options=TONES)
```

`render_field()` はこの設計図を見て、種類（`kind`）に合った入力欄を作ります。

```python
if f.kind == "text":
    return st.text_input(...)      # 1行の入力欄
if f.kind == "textarea":
    return st.text_area(...)       # 複数行の入力欄
if f.kind == "select":
    return st.selectbox(...)       # プルダウン
...
```

**設計図を読んで、それに合った家具を置いていく職人** のようなイメージです。

> 💡 この仕組みのおかげで、新しいツールを追加するときは `tools.py` に設計図を書くだけで済み、`app.py` は一切触らなくてよくなっています。

### 細かい工夫

- **必須項目にはラベルに `*` を付ける**（57行目）ので、必須項目がひと目で分かります
- **初期値は `st.session_state.setdefault(...)` で先にメモ帳へ入れておきます**（56行目）。入力欄に直接初期値を渡すと、②の仕組みとぶつかって Streamlit が警告を出すためです

---

## ④ サイドバー：`render_sidebar()`

📍 80行目〜

左側のサイドバーでは、3つのことをしています。

### 1. ツールを選ぶ

```python
tool_id = st.radio("ツール", [t.id for t in TOOLS], format_func=..., key="tool_id")
```

内部では `"blog"` や `"email"` のような ID で管理しています。`format_func` を使って、画面上では「📝 ブログ記事作成」のような分かりやすい名前に変換して表示しています。

### 2. APIキーとモデルを設定する

```python
api_key = input_key.strip() or env_key
```

`A or B` は「A が空なら B を使う」という書き方です。**画面で入力したキーを優先し、空なら `.env` のキーを使う**、という意味になります。

### 3. 履歴を表示する（`render_history()`）

`history` の中身を、`reversed` で新しい順に並べて表示します。

最後の行で、「選ばれたツール」「APIキー」「モデル名」の3つを `main()` に返します。

```python
return TOOL_MAP[tool_id], api_key, model
```

---

## ⑤ 「生成する」ボタンが押されたら

📍 `main()` の後半（232行目〜）

```python
with st.form(f"form__{tool.id}"):
    values = {f.key: render_field(tool, f) for f in tool.fields}
    submitted = st.form_submit_button("✨ 生成する", ...)
```

`st.form` は、**「送信ボタンを押すまで再実行しない」** ための入れ物です。これがないと、1文字入力するたびにアプリ全体が再実行されてしまいます。

`values` には、入力内容が次のような辞書の形でまとまります。

```python
{"topic": "在宅ワークのコツ", "tone": "親しみやすい", ...}
```

### 必須項目のチェック

ボタンが押されると、まず必須項目が埋まっているかチェックします。

```python
missing = [f.label for f in tool.fields
           if f.required and not str(values.get(f.key, "")).strip()]
```

空の必須項目があれば、エラーを出して止めます。

### 予約を入れる

問題がなければ、ここがポイントです。

```python
st.session_state.pending[tool.id] = [{"role": "user", "text": tool.build_prompt(values)}]
```

**すぐに AI を呼ぶのではなく、「予約」を入れるだけ** にしています。`tool.build_prompt(values)` が入力内容から AI への指示文（プロンプト）を組み立て、それを `pending` に置いておきます。

---

## ⑥ なぜ「予約」にするのか？

> ⭐ **このコードいちばんの工夫です。**

ボタンを押したら、すぐ AI を呼べばよさそうに見えます。わざわざ予約にしているのには理由があります。

このアプリには、生成を始めるボタンが **2種類** あります。

1. 上にある「✨ 生成する」ボタン
2. 結果の下にある「もっと短く」などの **調整ボタン**

問題は2つ目です。Streamlit は上から順番に画面を描くので、調整ボタンが描かれるのは結果の下、つまりページのいちばん最後です。そこで AI を呼ぶと、**新しい結果がページの一番下に表示されてしまいます。**

### 解決策：予約票を1か所に集める

どちらのボタンも「予約票（`pending`）を置くだけ」にしました。

```python
pending = st.session_state.pending.pop(tool.id, None)
if pending:
    run_generation(tool, pending, api_key, model)
```

`main()` のこの場所で予約票を受け取り（`pop` は「取り出して消す」）、**決まった場所で生成する** ようにしています。

> 🍳 **飲食店にたとえると…**
> どの席から注文しても、注文票はすべて厨房の同じ場所に集まり、料理人は1か所で調理する、というイメージです。

### 調整ボタンの仕組み

調整ボタンは `on_click`（押された瞬間に呼ばれる関数）で予約票を置きます。

```python
col.button(label, on_click=queue_refine, args=(tool.id, label))
```

`on_click` の関数は、**再実行が始まる前に** 呼ばれます。そのため、再実行されたときには予約票がすでに置いてあり、ページ上のほうで受け取れる、という流れです。

---

## ⑦ AI に書かせる：`run_generation()`

📍 151行目〜

### 文字が流れるように表示される仕組み

```python
text = st.write_stream(
    stream_generate(api_key, model, tool.system, messages, tool.temperature)
)
```

`stream_generate` は、Gemini が書いた文章を **少しずつ** 返してきます。`st.write_stream` はそれを受け取るたびに画面へ書き足していくので、**文字がリアルタイムで流れるように表示** されます。全部書き終わると、完成した文章が `text` に入ります。

### エラー対策

```python
except GeminiError as e:
    st.error(str(e), icon="🚨")
```

APIキーが間違っている、利用上限に達した、といったときは、日本語のエラーメッセージを表示します。

### 結果をメモ帳に保存する

```python
st.session_state.conversations[tool.id] = [*messages, {"role": "model", "text": text}]
```

`[*messages, ...]` は「今までのやり取りの最後に、AIの返事を1つ足す」という意味です。会話はこんな形で積み重なっていきます。

```text
user : （ブログ記事を書いて、というプロンプト）
model: （AIが書いた記事）
user : もっと短く
model: （短くした記事）
```

> 💡 「もっと短く」と頼んだとき、AI は **それまでのやり取りをすべて受け取る** ので、「何を短くするのか」が分かります。これが調整機能の仕組みです。

### 最後に描き直す

保存が終わると、最後に `st.rerun()` を呼びます。

生成中の画面は「流れていく途中の表示」なので、一度ホワイトボードを消して、完成した結果をきれいに描き直しています。

---

## ⑧ 結果を表示する：`render_result()`

📍 185行目〜

```python
output = conv[-1]["text"]   # 会話のいちばん最後 = 最新の結果
```

`[-1]` は「リストの最後の要素」という意味です。最新の AI の返事を取り出して表示します。

その下には、次のような便利機能が並んでいます。

| 機能 | 仕組み |
|---|---|
| 文字数の表示 | `len(output)` で数えている |
| ダウンロード | `st.download_button` で `.md` ファイルとして保存 |
| クリア | `conversations` からそのツールの会話を消す |
| コピー用テキスト | `st.code` で表示すると、右上にコピーボタンが自動で付く |
| 調整ボタン・自由入力欄 | ⑥で説明した「予約」を置くボタン |

---

## まとめ

### ボタンを押してから結果が出るまで

```text
① 「✨ 生成する」を押す
      ↓（Streamlit が上から再実行）
② 入力内容からプロンプトを作り、pending に予約を置く
      ↓
③ pending を取り出して run_generation() を実行
      ↓
④ Gemini の文章をリアルタイムで表示
      ↓
⑤ 結果を conversations と history に保存して st.rerun()
      ↓（もう一度、上から再実行）
⑥ render_result() が完成した結果を表示
```

### 押さえておきたい3つのポイント

1. **Streamlit は毎回上から全部やり直す。覚えておきたいものは `st.session_state` に入れる**
2. **入力画面は `tools.py` の設計図から自動で作られるので、ツールの追加に `app.py` の変更はいらない**
3. **生成は「予約（`pending`）→ 決まった場所で実行」の流れにして、どのボタンからでも同じ場所に結果が出るようにしている**

---

次は `tools.py`（各ツールのプロンプトの作り方）を読むと、「AI にどう指示を出しているか」が分かっておもしろいと思います。
