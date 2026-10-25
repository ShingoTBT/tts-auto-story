#!/usr/bin/env python3
"""
アカウント設定(YAML)を受け取り、Claude APIでストーリーを生成し、
index.phpのパーサーがそのまま読めるプレーンテキストとして出力する。

使い方:
    python generate.py accounts/account1_emotional.yaml

出力:
    {output_dir}/{YYYYMMDD_HHMMSS}.txt
"""

import sys
import os
import json
import re
import datetime
from pathlib import Path

import yaml
import anthropic


def claude_extra_params(model: str) -> dict:
    """モデルごとの追加指定。Haiku 5.5 は「考える処理」が既定でオンで、
    短い上限(要約の60トークンなど)を使い切って空の答えになるため、オフにする。
    それ以外のモデル(今の Sonnet 4.6)は何も足さない=今までどおり"""
    if model.startswith("claude-haiku-5"):
        return {"thinking": {"type": "disabled"}}
    return {}


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)



# 絵文字(肌の色・異体字セレクタ・ZWJ のつながりを含む)の直後の「。」
_EMOJI_PERIOD_RE = re.compile(
    "([\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF][\uFE0F\u200D\U0001F3FB-\U0001F3FF\U0001F000-\U0001FAFF\u2600-\u27BF]*)。(?=\n|$)"
)


_EN_NUMS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
            "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
            "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100}
# 日本語の文字のすぐ前にある英語の数(「six時」など)。「ONE OK ROCK」のように後ろが空白・英字のものは触らない
_EN_NUM_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(sorted(_EN_NUMS, key=len, reverse=True)) + r")(?=[぀-ヿ一-鿿])",
                        re.IGNORECASE)


def fix_english_numbers(text: str) -> str:
    """AI がたまに書く「毎朝six時」を「毎朝6時」に直す(10-25 アカウント6で投稿されてしまった。ユーザー指摘)"""
    def to_digit(m):
        print(f"  英語の数を直しました: {m.group(0)} → {_EN_NUMS[m.group(1).lower()]}")
        return str(_EN_NUMS[m.group(1).lower()])
    return _EN_NUM_RE.sub(to_digit, text)


def strip_period_after_emoji(text: str) -> str:
    """「しんどい🥺。」の「。」を消す(行の終わりにあるものだけ。文の途中はそのまま)"""
    return _EMOJI_PERIOD_RE.sub(r"\1", text)


_META_WORDS = r"以下|ルール|新規|作成|文字数|字数|構成|指示|プロンプト|ブロック|出力|本文|タイトル|ハッシュタグ|条件|注意|修正|書き直"
# かっこで囲まれた1行・※で始まる1行のうち、作業の説明の言葉を含むもの
_META_LINE_RE = re.compile(r"(?:[（(【\[〔][^）)】\]〕]*(?:" + _META_WORDS + r")[^）)】\]〕]*[）)】\]〕]|※.*(?:" + _META_WORDS + r").*)")
# 消したあとも残っていたら、投稿させずに作り直す言い回し
META_LEFTOVER_RE = re.compile(r"ルールに沿|新規作成|以下、|文字数|プロンプト|（注|\(注|構成案|ハッシュタグ\d|第\d?ブロック")


def find_meta_leftover(text: str):
    """本文(ハッシュタグ行を除く)に AI の作業メモらしい言い回しが残っていれば、その行を返す"""
    for line in text.split("\n"):
        if line.strip().startswith("#"):
            continue
        if META_LEFTOVER_RE.search(line):
            return line.strip()
    return None


LEAK_CHECK_MODEL = "claude-sonnet-4-6"
# AI の返事の決まり文句(投稿の中身として出てくることはまず無いもの)。AI の話題のニュースもあるので「AI」などの言葉自体は、別の AI の読み直しで中身から判断する
_LEAK_WORDS_RE = re.compile(r"承知しました|かしこまりました|言語モデル|AIアシスタント|ご依頼の")


def check_ai_leak(text: str):
    """投稿の中に「AI が作っている」「作業の裏側」がわかる文があれば、その文を返す(無ければ None)。
    2026-10-25〜 ユーザー「AIでやっていることがバレるようなことは絶対NG」。
    決まった言葉で探したうえで、別の AI にも1回読ませる(言葉だけでは拾えない独り言・作業メモ・前置きのため)"""
    hit = find_meta_leftover(text)
    if hit:
        return hit
    for line in text.split("\n"):
        if _LEAK_WORDS_RE.search(line):
            return line.strip()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    # 最後にタイトルをもう一度書くのは決まった形(投稿の説明文用)なので、読ませる前に外す
    rows = text.rstrip().split(chr(10))
    if len(rows) > 1 and rows[-1].strip() == rows[0].strip():
        text = chr(10).join(rows[:-1]).rstrip()
    prompt = (
        "次の文章は、SNS(TikTok・Threads)に本人の投稿としてそのまま載せる完成原稿です。\n"
        "読んだ人に「AI が作っている」「誰かの指示で作業して書いている」と気づかれる部分が1つでもないか確かめてください。\n"
        "例：作業メモ・注記（「以下、ルールに沿って」「新しく〇〇で」「文字数」など）、前置きや締めの独り言（「作成しました」「いかがでしょうか」）、"
        "指示・ルール・テーマ・切り口への言及、AI・生成・プロンプトへの言及。\n"
        "物語やニュースの中身として自然な文は問題ありません（例：登場人物がAIの話をしているニュースは可）。\n"
        "問題が無ければ「OK」だけ、あれば「NG：」に続けてその部分をそのまま書いてください。\n\n"
        "--- 原稿 ---\n" + text
    )
    try:
        r = anthropic.Anthropic(api_key=key).messages.create(
            model=LEAK_CHECK_MODEL, max_tokens=200, messages=[{"role": "user", "content": prompt}])
        answer = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        print(f"  AI の読み直しができませんでした(言葉での確認だけで進めます): {e}")
        return None
    if answer.startswith("NG"):
        return answer[3:].strip() or answer
    return None


def strip_meta_commentary(text: str) -> str:
    """
    AIが誤って出力に混入させがちな、自己チェック・メタ情報を除去する安全策。
    - [info]...[/info] のようなブロック
    - "文字数確認" を含む行
    - チェックマーク(✅❌)を含む行
    - "---" のみの区切り線
    - 「今回は〜の切り口で作成します」のような、話題選定の前置き・独り言
    """
    # [xxx]...[/xxx] 形式のブロックを除去(info/note/check等、名前を問わず)
    text = re.sub(r"\[(\w+)\][\s\S]*?\[/\1\]", "", text, flags=re.IGNORECASE)

    # 「話題は直近で使用済みなので、今回は〜の切り口で作成します」のような、
    # 話題選定の説明・前置き文を、段落単位で除去する
    # (「今回は」「作成します」「書きます」「使用済み」等、メタ的な単語の組み合わせで判定)
    paragraphs = text.split("\n\n")
    filtered_paragraphs = []
    meta_intro_markers = ["作成します", "書きます」", "投稿を作成", "使用済みなので", "の切り口で", "重複を避け"]
    for i, para in enumerate(paragraphs):
        para_stripped = para.strip()
        # 最初の1〜2段落のみ対象(本文中に紛れ込んだ通常の文章を誤検知しないため)
        if i <= 1 and any(marker in para_stripped for marker in meta_intro_markers) and "今回は" in para_stripped:
            continue
        filtered_paragraphs.append(para)
    text = "\n\n".join(filtered_paragraphs)

    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            continue
        if "文字数確認" in stripped or "✅" in stripped or "❌" in stripped:
            continue
        if re.fullmatch(r"-{2,}", stripped):
            continue
        # 10-25 「（以下、ルールに沿って新規作成）」のような、AI の作業メモの行(ユーザー「絶対ダメ」)
        if _META_LINE_RE.fullmatch(stripped):
            print(f"  作業メモの行を消しました: {stripped}")
            continue
        cleaned.append(line)

    # 10-25 最初の区切りより前に「■」の行が2つ以上ある = 前の方は AI の独り言(「■…はもう書かない。新しく〇〇で。」)。
    # 最後の「■」の行から始める
    first_sep = next((i for i, l in enumerate(cleaned) if re.match(r"^\s*={2,}\s*$", l)), len(cleaned))
    heads = [i for i in range(first_sep) if cleaned[i].strip().startswith("■")]
    if len(heads) >= 2:
        print(f"  タイトルの前の独り言を消しました: {' / '.join(l.strip() for l in cleaned[:heads[-1]] if l.strip())}")
        cleaned = cleaned[heads[-1]:]

    result = "\n".join(cleaned)
    # 除去した結果できた3連続以上の空行を整理
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def load_recent_titles(titles_log_path: str, count: int) -> list[str]:
    path = Path(titles_log_path)
    if not path.exists():
        return []

    titles = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                titles.append(record.get("title", ""))
            except json.JSONDecodeError:
                continue

    return titles[-count:]


def append_title_log(titles_log_path: str, title: str) -> None:
    path = Path(titles_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "title": title,
        "generated_at": datetime.datetime.now().isoformat(),
    }

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_system_prompt(prompt_file: str) -> str:
    with open(prompt_file, "r", encoding="utf-8") as f:
        return f.read()


def build_user_message(recent_titles: list[str]) -> str:
    base = (
        "新しい感動ストーリーを1本、指定フォーマット・全ルールを厳守して生成してください。\n"
        "テーマ：家族愛に関するもの、または涙を誘うものを中心に選んでください。"
    )

    if not recent_titles:
        return base

    titles_block = "\n".join(f"- {t}" for t in recent_titles)
    return (
        f"{base}\n\n"
        "以下は直近に使用済みのタイトル一覧です。内容・切り口ともに重複や類似がないようにしてください。\n"
        f"{titles_block}"
    )


def call_claude(system_prompt: str, user_message: str, model: str) -> str:
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY環境変数を自動参照

    response = client.messages.create(
        model=model,
        **claude_extra_params(model),
        max_tokens=2000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    # テキストブロックのみ連結
    parts = [block.text for block in response.content if block.type == "text"]
    return "".join(parts).strip()


def validate_output_format(text: str) -> tuple[bool, str]:
    """
    index.phpのパーサーが期待する最低限の形式かをチェックする。
    - タイトル行(1行目)が空でない
    - "==" 区切り行が2つ以上ある
    - 最終行付近に # で始まるハッシュタグ行がある
    """
    lines = text.split("\n")

    if not lines or lines[0].strip() == "":
        return False, "1行目のタイトルが空です。"
    # タイトルは体言止めの一言。「。」があったり長すぎたりするのは、AI の独り言が入ったもの
    if "。" in lines[0] or len(lines[0].strip()) > 40:
        return False, f"1行目がタイトルらしくありません: 「{lines[0].strip()}」"

    separator_count = sum(1 for line in lines if re.match(r"^\s*={2,}\s*$", line))
    if separator_count < 2:
        return False, f"'==' 区切り行が{separator_count}個しかありません（3ブロックには2個必要）。"

    hashtag_lines = [line for line in lines if line.strip().startswith("#")]
    if not hashtag_lines:
        return False, "ハッシュタグ行(#始まり)が見つかりません。"

    leftover = find_meta_leftover(text)
    if leftover:
        return False, f"AI の作業メモらしい文が残っています: 「{leftover}」"

    return True, "OK"


def use_queued_post(config: dict) -> bool:
    """ユーザーが見本で確認済みの投稿(文章＋動画)が <output_dir>/queue_next/ にあれば、新しく作らずにそれを使う。
    1回使うと消える(2026-10-23〜 動画投稿の最初の1本に見本を使うため)"""
    import shutil
    q = Path(config["output_dir"]) / "queue_next"
    txts = sorted(q.glob("*.txt")) if q.exists() else []
    if not txts:
        return False
    src = txts[0]
    output_dir = Path(config["output_dir"])
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"{timestamp}.txt"
    shutil.move(str(src), output_path)
    video = q / "videos" / src.stem
    if video.exists():
        dest = output_dir / "videos" / timestamp
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(video), dest)
    title = extract_title(output_path.read_text(encoding="utf-8"))
    append_title_log(config["titles_log_file"], title)
    print(f"見本で確認済みの投稿を使います: {output_path}")
    print(f"タイトル: {title}")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"output_path={output_path}\n")
    return True


def extract_title(text: str) -> str:
    lines = text.split("\n")
    return lines[0].strip() if lines else ""


def main():
    if len(sys.argv) < 2:
        print("使い方: python generate.py <account_config.yaml>")
        sys.exit(1)

    config_path = sys.argv[1]
    config = load_account_config(config_path)
    if use_queued_post(config):
        return

    system_prompt = load_system_prompt(config["prompt_file"])
    recent_titles = load_recent_titles(
        config["titles_log_file"], config.get("recent_titles_count", 30)
    )
    user_message = build_user_message(recent_titles)

    # フォーマット不正時は最大3回までリトライ
    max_retries = 3
    output_text = ""
    for attempt in range(1, max_retries + 1):
        output_text = call_claude(system_prompt, user_message, config["model"])
        output_text = strip_meta_commentary(output_text)
        output_text = strip_period_after_emoji(output_text)
        output_text = fix_english_numbers(output_text)
        is_valid, message = validate_output_format(output_text)
        if is_valid:
            leak = check_ai_leak(output_text)
            if leak:
                is_valid, message = False, f"AI が作っているとわかる文があるので作り直します: 「{leak}」"

        if is_valid:
            break

        print(f"[試行{attempt}] フォーマット不正: {message}")
        if attempt == max_retries:
            print("最大リトライ回数に達しました。デバッグ用に最後の出力を保存して中止します。")
            debug_dir = Path(config["output_dir"])
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_path = debug_dir / f"debug_failed_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(f"[エラー: {message}]\n\n--- 生成された内容 ---\n\n{output_text}")
            print(f"デバッグファイル: {debug_path}")
            sys.exit(1)

    # 出力先ディレクトリ作成
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"{timestamp}.txt"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output_text)

    # タイトルログに追記(重複防止用)
    title = extract_title(output_text)
    append_title_log(config["titles_log_file"], title)

    print(f"生成完了: {output_path}")
    print(f"タイトル: {title}")

    # GitHub Actions側で後続ステップ(画像化・通知)がこのファイルを
    # 参照できるよう、GITHUB_OUTPUTにパスを書き出しておく
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"output_path={output_path}\n")


if __name__ == "__main__":
    main()
