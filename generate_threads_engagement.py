#!/usr/bin/env python3
"""
threads_engagement_v1.mdプロンプトで、共感/議論を呼ぶ創作エピソード投稿を生成する。
ニュース取得は不要(フィクションの創作のため)。

使い方:
    python generate_threads_engagement.py accounts/account4_threads_engagement.yaml
"""

import sys
import os
import re
import datetime
from pathlib import Path

import yaml
import anthropic

from generate import load_recent_titles, append_title_log, load_system_prompt


def enforce_period_linebreaks(text: str) -> str:
    """句点(。)のたびに改行する(すでに改行済みなら二重にしない)"""
    return re.sub(r"。(?!\n)", "。\n", text)


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_user_message(recent_titles: list[str]) -> str:
    base = "指定要件を厳守して、共感/議論を呼ぶ投稿を1本、創作してください。"
    if recent_titles:
        titles_block = "\n".join(f"- {t}" for t in recent_titles)
        base += (
            "\n\n以下は直近に投稿済みの話題の要約です。同じシチュエーション・同じ切り口の"
            "重複がないようにしてください。\n"
            f"{titles_block}"
        )
    return base


def call_claude(system_prompt: str, user_message: str, model: str) -> str:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    parts = [b.text for b in response.content if b.type == "text"]
    return "".join(parts).strip()


def validate_length(text: str, min_chars: int, max_chars: int) -> tuple[bool, str]:
    length = len(text)
    if length < min_chars:
        return False, f"文字数が{length}文字で、下限{min_chars}文字を下回っています"
    if length > max_chars:
        return False, f"文字数が{length}文字で、上限{max_chars}文字を超えています"
    return True, "OK"


def summarize_for_dedup(text: str, model: str) -> str:
    """投稿本文から、重複チェック用の短い要約(シチュエーションの型)をClaudeに作らせる"""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=60,
        system="与えられた投稿のシチュエーションを、10〜20文字程度の一言で要約してください。説明は不要です。",
        messages=[{"role": "user", "content": text}],
    )
    parts = [b.text for b in response.content if b.type == "text"]
    return "".join(parts).strip()


def main():
    if len(sys.argv) < 2:
        print("使い方: python generate_threads_engagement.py <account_config.yaml>")
        sys.exit(1)

    config = load_account_config(sys.argv[1])

    system_prompt = load_system_prompt(config["prompt_file"])
    recent_titles = load_recent_titles(
        config["titles_log_file"], config.get("recent_titles_count", 30)
    )
    user_message = build_user_message(recent_titles)

    min_chars = config.get("min_chars", 200)
    max_chars = config.get("max_chars", 300)

    max_retries = 5
    output_text = ""
    for attempt in range(1, max_retries + 1):
        output_text = call_claude(system_prompt, user_message, config["model"])
        output_text = enforce_period_linebreaks(output_text)
        is_valid, msg = validate_length(output_text, min_chars, max_chars)
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

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output_text)

    summary = summarize_for_dedup(output_text, config["model"])
    append_title_log(config["titles_log_file"], summary)

    print(f"生成完了: {output_path}")
    print(f"文字数: {len(output_text)}")
    print(f"シチュエーション要約: {summary}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"output_path={output_path}\n")


if __name__ == "__main__":
    main()
