"""このアプリのセキュリティ自動スキャン（手がかり集め用）。

プロジェクトのルートで、仮想環境の Python から実行する:
    .venv\\Scripts\\python.exe .claude/skills/app-security-check/scripts/scan.py

結果は「確認すべき候補」の一覧で、結論ではない。誤検知も見逃しもあるので、
最終的な判断は必ずコードを読んで行う。秘密情報の値は伏せて表示する。
外部ライブラリは使わず、ネットワークにも接続しない。
"""

import argparse
import ast
import importlib.metadata
import re
import subprocess
import sys
import tomllib
from pathlib import Path

# 画面の文字コードで表示できない文字があっても止まらないようにする
sys.stdout.reconfigure(errors="replace")

SKILL_DIR = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "venv", "env", "__pycache__", ".git", "node_modules",
             ".mypy_cache", ".pytest_cache", ".ruff_cache"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".json", ".yaml", ".yml", ".cfg",
                 ".ini", ".env", ".example", ".html", ".js", ".ps1", ".bat", ".sh"}

SECRET_PATTERNS = [
    ("Google (Gemini) APIキー", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("Anthropic APIキー", re.compile(r"sk-ant-[0-9A-Za-z_\-]{20,}")),
    ("OpenAI APIキー", re.compile(r"sk-(?!ant-)[0-9A-Za-z_\-]{20,}")),
]
# api_key = "..." のような直書き（.py のみ）
ASSIGN_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|token|password)\w*\s*[:=]\s*[\"']([^\"'\s]{12,})[\"']"
)
PLACEHOLDER_WORDS = ("your", "xxx", "example", "dummy", "placeholder", "here", "fake", "...")

# (説明, 正規表現, 目安の重要度)
CODE_PATTERNS = [
    ("HTML をそのまま描画（XSS の可能性）", r"unsafe_allow_html\s*=\s*True", "要注意"),
    ("st.html で HTML を描画", r"\bst\.html\(", "要注意"),
    ("components で HTML/iframe を埋め込み", r"components\.(html|iframe)\(", "要注意"),
    ("eval / exec（文字列をコードとして実行）", r"(?<![\w.])(eval|exec)\(", "要注意"),
    ("外部コマンドの実行", r"\bsubprocess\b|\bos\.(system|popen)\(", "要確認"),
    ("pickle の読み込み（信頼できないデータだと危険）", r"\bpickle\.loads?\(", "要確認"),
    ("yaml.load（SafeLoader 以外は危険）", r"\byaml\.load\(", "要確認"),
    ("変数を Markdown として描画（AI の出力なら画像・リンクによる情報持ち出しを確認）",
     r"\.markdown\(\s*(?!f?[\"'])", "要確認"),
    ("例外の中身を画面に表示", r"\.(error|warning|exception|write)\(.*\{e\}|\bst\.exception\(", "要確認"),
    ("APIキーを表示・出力している可能性",
     r"(print|logging\.\w+|logger\.\w+|st\.(write|text|code|markdown|json|caption))\(.*api_key", "要注意"),
    ("session_state を丸ごと表示", r"st\.(write|json)\(\s*st\.session_state\s*\)", "要注意"),
    ("キャッシュ（全セッション・全ユーザーで共有される）",
     r"@st\.cache_(data|resource)|@(functools\.)?(lru_)?cache\b", "情報"),
    ("ユーザー入力からファイルパスを作っている可能性", r"\bopen\(\s*(?!f?[\"'])", "要確認"),
]

counts = {"要注意": 0, "要確認": 0, "OK": 0, "情報": 0}


def report(level: str, msg: str) -> None:
    counts[level] = counts.get(level, 0) + 1
    print(f"  [{level}] {msg}")


def section(title: str) -> None:
    print(f"\n== {title} ==")


def mask(value: str) -> str:
    return value[:4] + "*" * 8 + f"（{len(value)}文字）"


def is_placeholder(value: str) -> bool:
    low = value.lower()
    return any(w in low for w in PLACEHOLDER_WORDS)


def iter_files(root: Path):
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS or part.endswith("-workspace") for part in rel.parts):
            continue
        if path.is_relative_to(SKILL_DIR):
            continue  # このスキル自身（検出用の文字列を含むため）
        if path.is_file():
            yield path, rel


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def rel_str(rel: Path) -> str:
    return rel.as_posix()


# ---------------------------------------------------------------------------
# A. APIキー・秘密情報
# ---------------------------------------------------------------------------
def gitignore_lines(root: Path) -> list[str]:
    text = read_text(root / ".gitignore") or ""
    return [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]


def is_ignored(name: str, lines: list[str]) -> bool:
    candidates = {name, f"/{name}", f"*{Path(name).suffix}" if Path(name).suffix else None,
                  f"{name}*", "*.env" if name == ".env" else None}
    return any(l in candidates for l in lines)


def git(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def check_secrets(root: Path) -> None:
    section("A. APIキー・秘密情報")

    # ファイル内の直書き
    found = False
    for path, rel in iter_files(root):
        if rel.name == ".env":
            continue  # .env は下で別に確認する（キーを置く正しい場所のため）
        if path.suffix.lower() not in TEXT_SUFFIXES and not rel.name.startswith(".env"):
            continue
        text = read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pat in SECRET_PATTERNS:
                for m in pat.finditer(line):
                    if is_placeholder(m.group(0)):
                        continue
                    found = True
                    report("要注意", f"{rel_str(rel)}:{lineno} に {name} らしき文字列: {mask(m.group(0))}")
            if path.suffix == ".py":
                m = ASSIGN_PATTERN.search(line)
                if m and not is_placeholder(m.group(2)) and not any(p.search(line) for _, p in SECRET_PATTERNS):
                    found = True
                    report("要確認", f"{rel_str(rel)}:{lineno} で {m.group(1)} に文字列を直接代入: {mask(m.group(2))}")
    if not found:
        report("OK", ".env 以外のファイルに APIキーらしき文字列は見つからなかった")

    # .env
    ignore = gitignore_lines(root)
    env = root / ".env"
    if env.exists():
        text = read_text(env) or ""
        for line in text.splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            k, v = (s.strip().strip("\"'") for s in line.split("=", 1))
            if re.search(r"(?i)key|secret|token|password", k) and v:
                kind = "実際のキーの形式" if SECRET_PATTERNS[0][1].fullmatch(v) else "形式は不明"
                report("情報", f".env に {k} が設定されている（{mask(v)}、{kind}）")
    else:
        report("情報", ".env は存在しない（キーはサイドバー入力、または未設定）")

    if is_ignored(".env", ignore):
        report("OK", ".gitignore に .env が含まれている")
    else:
        report("要注意", ".gitignore に .env が含まれていない（Git に APIキーが入る危険）")

    for name in (".streamlit/secrets.toml",):
        if (root / name).exists():
            if any(l.rstrip("/") in (name, f"/{name}", "secrets.toml", ".streamlit") for l in ignore):
                report("OK", f"{name} は .gitignore 済み")
            else:
                report("要注意", f"{name} があるが .gitignore に入っていない")

    ex = root / ".env.example"
    if ex.exists():
        text = read_text(ex) or ""
        if any(p.search(text) and not is_placeholder(p.search(text).group(0)) for _, p in SECRET_PATTERNS):
            report("要注意", ".env.example に本物らしいキーが入っている")
        else:
            report("OK", ".env.example にはダミー値だけが入っている")

    # Git
    if (root / ".git").exists():
        if git(root, "ls-files", "--error-unmatch", ".env") is not None:
            report("要注意", ".env が Git で管理（追跡）されている")
        hist = git(root, "log", "--all", "--format=%h %s", "--", ".env")
        if hist and hist.strip():
            report("要注意", f".env が過去にコミットされている: {hist.strip().splitlines()[:3]}")
        hist = git(root, "log", "--all", "--format=%h %s", "-G", r"AIza[0-9A-Za-z_-]{35}")
        if hist and hist.strip():
            report("要注意", f"Git の履歴に APIキーらしき文字列を含むコミット: {hist.strip().splitlines()[:3]}")
        elif hist is not None:
            report("OK", "Git の履歴に APIキーらしき文字列は見つからなかった")
    else:
        report("情報", "Git リポジトリではない（Git 関連の確認は省略）")


# ---------------------------------------------------------------------------
# B. アプリのコード
# ---------------------------------------------------------------------------
def py_files(root: Path):
    for path, rel in iter_files(root):
        if path.suffix == ".py" and rel.parts[0] not in (".claude", ".agents"):
            yield path, rel


def check_code(root: Path) -> None:
    section("B. アプリのコード（該当行の一覧。前後のコードを読んで判断すること）")
    compiled = [(d, re.compile(p), lv) for d, p, lv in CODE_PATTERNS]
    any_hit = False
    for path, rel in py_files(root):
        text = read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for desc, pat, level in compiled:
                if pat.search(line):
                    any_hit = True
                    report(level, f"{rel_str(rel)}:{lineno} {desc}\n           > {line.strip()[:120]}")
        check_key_inputs(text, rel)
    if not any_hit:
        report("OK", "注意が必要なコードのパターンは見つからなかった")


def check_key_inputs(text: str, rel: Path) -> None:
    """キーやパスワードの入力欄が type="password" になっているか。"""
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        report("要確認", f"{rel_str(rel)} を構文解析できなかった: {e}")
        return
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "text_input" and node.args):
            continue
        label = node.args[0]
        if not (isinstance(label, ast.Constant) and isinstance(label.value, str)):
            continue
        if not re.search(r"(?i)key|キー|password|パスワード|token|secret", label.value):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        t = kw.get("type")
        if isinstance(t, ast.Constant) and t.value == "password":
            report("OK", f"{rel_str(rel)}:{node.lineno} 「{label.value}」の入力欄は type=\"password\"")
        else:
            report("要注意", f"{rel_str(rel)}:{node.lineno} 「{label.value}」の入力欄が type=\"password\" ではない（画面にキーが見える）")


# ---------------------------------------------------------------------------
# C. 起動・公開の設定
# ---------------------------------------------------------------------------
def load_toml(path: Path) -> dict | None:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        report("要確認", f"{path} を読み込めなかった: {e}")
        return None


def check_config(root: Path) -> None:
    section("C. 起動・公開の設定（Streamlit）")
    merged: dict = {}
    # 後から読んだもの（プロジェクト側）が優先される
    for path in (Path.home() / ".streamlit" / "config.toml", root / ".streamlit" / "config.toml"):
        if path.exists():
            report("情報", f"設定ファイルあり: {path}")
            data = load_toml(path) or {}
            for sec, vals in data.items():
                if isinstance(vals, dict):
                    merged.setdefault(sec, {}).update(vals)
    if not merged:
        report("情報", "Streamlit の設定ファイル（config.toml）はない＝すべて標準設定")

    server = merged.get("server", {})
    address = server.get("address")
    if address in ("localhost", "127.0.0.1", "::1"):
        report("OK", f"server.address = {address}（このパソコンからしか開けない）")
    elif address in (None, ""):
        report("要注意", "server.address が未設定: Streamlit はすべてのネットワークで待ち受けるため、"
                        "同じ Wi-Fi などの他人からも開ける（起動コマンドで --server.address を指定していない場合）")
    else:
        report("要注意", f"server.address = {address}（このパソコン以外からも開ける設定）")

    if server.get("enableXsrfProtection") is False:
        report("要注意", "server.enableXsrfProtection = false（XSRF 対策が無効）")
    if server.get("enableCORS") is False:
        report("要確認", "server.enableCORS = false（他のサイトからのアクセス制限が無効）")
    if merged.get("browser", {}).get("gatherUsageStats") is not False:
        report("情報", "browser.gatherUsageStats が false ではない（Streamlit に利用統計が送られる。入力した文章は送られない）")
    if merged.get("client", {}).get("showErrorDetails") in (None, True, "full"):
        report("情報", "client.showErrorDetails が標準のまま（想定外のエラー時に詳細が画面に出る）")

    # 起動方法の手がかり
    for name in ("README.md", "CLAUDE.md"):
        text = read_text(root / name) or ""
        for lineno, line in enumerate(text.splitlines(), 1):
            if "streamlit run" in line:
                opt = "--server.address" in line
                report("情報", f"{name}:{lineno} の起動コマンド: {line.strip()}"
                              + ("" if opt else "（--server.address の指定なし）"))


# ---------------------------------------------------------------------------
# D. ライブラリ
# ---------------------------------------------------------------------------
def check_deps(root: Path) -> None:
    section("D. ライブラリ（ネット接続が必要な確認は行っていない）")
    print(f"  使用中の Python: {sys.executable}")
    if ".venv" not in sys.executable:
        report("要確認", "仮想環境（.venv）の Python ではないため、下のバージョンはアプリ実行時と異なる可能性がある")
    req = root / "requirements.txt"
    if not req.exists():
        report("要確認", "requirements.txt がない")
        return
    for line in (read_text(req) or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"([A-Za-z0-9_.\-\[\]]+)\s*(.*)", line)
        name, spec = m.group(1), m.group(2)
        try:
            installed = importlib.metadata.version(re.sub(r"\[.*\]", "", name))
        except importlib.metadata.PackageNotFoundError:
            installed = "未インストール"
        if not spec:
            note, level = "バージョン指定なし", "情報"
        elif spec.startswith("=="):
            note, level = "固定（古いまま放置されていないか確認）", "情報"
        else:
            note, level = "下限のみ（インストールのたびに最新が入る）", "情報"
        report(level, f"{name} {spec or ''} → インストール済み {installed}（{note}）")
    print("  ※ 古いライブラリ・既知の脆弱性の確認は、SKILL.md の手順（pip list --outdated など）で別途行う")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="プロジェクトのフォルダ（省略時はカレント）")
    root = Path(parser.parse_args().root).resolve()
    print(f"セキュリティ自動スキャン: {root}")
    print("（これは手がかりの一覧。誤検知・見逃しがあるので、必ずコードを読んで判断する）")
    check_secrets(root)
    check_code(root)
    check_config(root)
    check_deps(root)
    print("\n== 集計 ==")
    print("  " + " / ".join(f"{k}: {v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
