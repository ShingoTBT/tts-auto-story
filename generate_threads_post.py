#!/usr/bin/env python3
"""
fetch_trending_news.pyが出力したtrend_candidate.jsonを読み込み、
threads_news_v1.mdプロンプトでThreads向けのテキスト投稿を生成する。

使い方:
    python generate_threads_post.py accounts/account3_threads.yaml
"""

import sys
import os
import json
import re
import datetime
from pathlib import Path

import yaml
import anthropic

from generate import load_recent_titles, append_title_log, load_system_prompt, strip_meta_commentary


def enforce_period_linebreaks(text: str) -> str:
    """句点(。)のたびに改行する(すでに改行済みなら二重にしない)"""
    return re.sub(r"。(?!\n)", "。\n", text)


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_trend_candidate(output_dir: str) -> dict:
    path = Path(output_dir) / "trend_candidate.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_user_message(candidate: dict, recent_titles: list[str]) -> str:
    base = (
        "以下のトレンドキーワードと記事本文をもとに、指定フォーマット・文字数ルールを"
        "厳守してThreads投稿を1本生成してください。\n\n"
        f"【トレンドキーワード】\n{candidate['keyword']}\n\n"
        f"【元記事本文】\n{candidate['article_text']}"
    )
    if recent_titles:
        titles_block = "\n".join(f"- {t}" for t in recent_titles)
        base += (
            "\n\n以下は直近に投稿済みの話題の一覧です。同じ話題の重複がないか確認してください。\n"
            f"{titles_block}"
        )
    return base


def call_claude(system_prompt: str, user_message: str, model: str) -> str:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    parts = [block.text for block in response.content if block.type == "text"]
    return "".join(parts).strip()


def validate_length(text: str, min_chars: int, max_chars: int) -> tuple[bool, str]:
    length = len(text)
    if length < min_chars:
        return False, f"文字数が{length}文字で、下限{min_chars}文字を下回っています"
    if length > max_chars:
        return False, f"文字数が{length}文字で、上限{max_chars}文字を超えています"
    return True, "OK"


def validate_hashtag_count(text: str) -> tuple[bool, str]:
    lines = [l for l in text.split("\n") if l.strip().startswith("#")]
    if not lines:
        return False, "ハッシュタグ行が見つかりません"
    import re
    tag_count = len(re.findall(r"#\S+", lines[-1]))
    if tag_count != 3:
        return False, f"ハッシュタグ数が{tag_count}個です(3個である必要があります)"
    return True, "OK"


def validate_question_ending(text: str) -> tuple[bool, str]:
    """ハッシュタグ行を除いた本文の最終行が、読者を名指しした疑問形で終わっているかを確認する"""
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.strip().startswith("#")]
    if not lines:
        return False, "本文が見つかりません"
    last_line = lines[-1]

    if "？" not in last_line and "?" not in last_line:
        return False, f"本文の最後が疑問形(？)で終わっていません: 「{last_line}」"

    reader_markers = [
        "みなさん", "皆さん", "あなたは", "あなたの", "みんなは", "みんなの",
        "な人", "た人", "する人", "派の人", "経験ある", "見た人", "行った人",
        "使った人", "知ってる人", "います？", "いますか", "どっち",
    ]
    if not any(marker in last_line for marker in reader_markers):
        return False, f"読者を名指しした疑問文になっていません(自分完結型の独り言の可能性): 「{last_line}」"

    return True, "OK"


def validate_casual_tone(text: str) -> tuple[bool, str]:
    """カジュアルな口語表現が最低2箇所以上含まれているかを確認する"""
    casual_markers = [
        "マジ", "わんちゃん", "って感じ", "だよね", "なんだって",
        "本当かな", "ガチ", "エグ", "ヤバ", "めちゃくちゃ", "めっちゃ",
    ]
    count = sum(text.count(marker) for marker in casual_markers)
    if count < 2:
        return False, f"カジュアルな口語表現が{count}箇所しか見つかりません(最低2箇所必要)"
    return True, "OK"


def main():
    if len(sys.argv) < 2:
        print("使い方: python generate_threads_post.py <account_config.yaml>")
        sys.exit(1)

    config = load_account_config(sys.argv[1])
    candidate = load_trend_candidate(config["output_dir"])

    system_prompt = load_system_prompt(config["prompt_file"])
    recent_titles = load_recent_titles(
        config["titles_log_file"], config.get("recent_titles_count", 30)
    )
    user_message = build_user_message(candidate, recent_titles)

    min_chars = config.get("min_chars", 350)
    max_chars = config.get("max_chars", 450)

    max_retries = 7
    output_text = ""
    for attempt in range(1, max_retries + 1):
        output_text = call_claude(system_prompt, user_message, config["model"])
        output_text = strip_meta_commentary(output_text)
        output_text = enforce_period_linebreaks(output_text)

        is_valid, msg = validate_length(output_text, min_chars, max_chars)
        if is_valid:
            is_valid, msg = validate_hashtag_count(output_text)
        if is_valid:
            is_valid, msg = validate_question_ending(output_text)
        if is_valid:
            is_valid, msg = validate_casual_tone(output_text)
        if is_valid:
            break

        print(f"[試行{attempt}] {msg}")
        if attempt == max_retries:
            debug_dir = Path(config["output_dir"])
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_path = debug_dir / f"debug_failed_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(f"[エラー: {msg}]\n\n--- 生成内容 ---\n\n{output_text}")
            print(f"デバッグファイル: {debug_path}")
            sys.exit(1)

    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"{timestamp}.txt"

    # Threads側で「段落先頭の#」が認識されない現象への対策として、
    # ハッシュタグ行の先頭に半角スペースを1つ入れる
    lines = output_text.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip().startswith("#"):
            if not lines[i].startswith(" "):
                lines[i] = " " + lines[i]
            break
    output_text = "\n".join(lines)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output_text)

    # 話題の重複防止用に、トレンドキーワードをログに残す
    append_title_log(config["titles_log_file"], candidate["keyword"])

    print(f"生成完了: {output_path}")
    print(f"文字数: {len(output_text)}")
    print(f"元ネタ: {candidate['keyword']} / {candidate['article_url']}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"output_path={output_path}\n")


if __name__ == "__main__":
    main()
