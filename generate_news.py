#!/usr/bin/env python3
"""
fetch_trending_news.pyが出力したtrend_candidate.jsonを読み込み、
news_v1.mdプロンプトでClaude APIに投稿文を生成させる。

使い方:
    python generate_news.py accounts/account2_news.yaml
"""

import sys
import os
import json
import re
import datetime
from pathlib import Path

import yaml
import anthropic

from generate import (
    load_recent_titles,
    append_title_log,
    load_system_prompt,
    validate_output_format,
    extract_title,
)


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_trend_candidate(output_dir: str) -> dict:
    path = Path(output_dir) / "trend_candidate.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_user_message(candidate: dict, recent_titles: list[str]) -> str:
    base = (
        "以下のトレンドキーワードと記事本文をもとに、指定フォーマット・全ルール"
        "（特にハッシュタグのルール）を厳守して投稿を1本生成してください。\n\n"
        f"【トレンドキーワード】\n{candidate['keyword']}\n\n"
        f"【元記事本文】\n{candidate['article_text']}"
    )

    if recent_titles:
        titles_block = "\n".join(f"- {t}" for t in recent_titles)
        base += (
            "\n\n以下は直近に使用済みのタイトル一覧です。同じ話題の重複がないか確認してください。\n"
            f"{titles_block}"
        )

    return base


def call_claude(system_prompt: str, user_message: str, model: str) -> str:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    parts = [block.text for block in response.content if block.type == "text"]
    return "".join(parts).strip()


def validate_hashtag_count(text: str) -> tuple[bool, str]:
    lines = [l for l in text.split("\n") if l.strip().startswith("#")]
    if not lines:
        return False, "ハッシュタグ行が見つかりません"
    tag_count = len(re.findall(r"#\S+", lines[0]))
    if tag_count != 5:
        return False, f"ハッシュタグ数が{tag_count}個です(5個である必要があります)"
    return True, "OK"


def main():
    if len(sys.argv) < 2:
        print("使い方: python generate_news.py <account_config.yaml>")
        sys.exit(1)

    config = load_account_config(sys.argv[1])
    candidate = load_trend_candidate(config["output_dir"])

    system_prompt = load_system_prompt(config["prompt_file"])
    recent_titles = load_recent_titles(
        config["titles_log_file"], config.get("recent_titles_count", 30)
    )
    user_message = build_user_message(candidate, recent_titles)

    max_retries = 3
    output_text = ""
    for attempt in range(1, max_retries + 1):
        output_text = call_claude(system_prompt, user_message, config["model"])
        # タイトル行に誤って角括弧が含まれた場合の安全策として除去
        lines = output_text.split("\n")
        if lines:
            lines[0] = lines[0].replace("[", "").replace("]", "")
        output_text = "\n".join(lines)

        is_valid, msg = validate_output_format(output_text)
        if is_valid:
            is_valid_tags, tag_msg = validate_hashtag_count(output_text)
            if is_valid_tags:
                break
            msg = tag_msg
            is_valid = False

        print(f"[試行{attempt}] フォーマット不正: {msg}")
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

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output_text)

    title = extract_title(output_text)
    append_title_log(config["titles_log_file"], f"{title} ({candidate['keyword']})")

    print(f"生成完了: {output_path}")
    print(f"タイトル: {title}")
    print(f"元ネタ: {candidate['keyword']} / {candidate['article_url']}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"output_path={output_path}\n")


if __name__ == "__main__":
    main()
