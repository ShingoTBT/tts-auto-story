#!/usr/bin/env python3
"""
tiktok_engagement_v1.mdプロンプトで、1画像に収まる共感/議論系投稿を生成する。

使い方:
    python generate_tiktok_engagement.py accounts/account5_tiktok_engagement.yaml
"""

import sys
import os
import re
import datetime
from pathlib import Path

import yaml
import anthropic

from generate import load_recent_titles, append_title_log, load_system_prompt, extract_title


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_user_message(recent_titles: list[str]) -> str:
    base = "指定要件・出力フォーマットを厳守して、共感/議論を呼ぶ投稿を1本、創作してください。"
    if recent_titles:
        titles_block = "\n".join(f"- {t}" for t in recent_titles)
        base += (
            "\n\n以下は直近に投稿済みの話題です。同じシチュエーション・同じ切り口の重複がないようにしてください。\n"
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


def validate_output(text: str, min_chars: int, max_chars: int) -> tuple[bool, str]:
    lines = text.split("\n")
    if not lines or not lines[0].strip():
        return False, "1行目のタイトルが空です"

    if not re.search(r"^\s*={2,}\s*$", text, re.MULTILINE):
        return False, "区切り線(=====)が見つかりません(2ブロック構成には1つ必要です)"

    hashtag_lines = [l for l in lines if l.strip().startswith("#")]
    if not hashtag_lines:
        return False, "ハッシュタグ行が見つかりません"
    tag_count = len(re.findall(r"#\S+", hashtag_lines[0]))
    if tag_count != 3:
        return False, f"ハッシュタグ数が{tag_count}個です(3個である必要があります)"

    # 本文(タイトル行・ハッシュタグ行・タイトル再掲行を除く)の文字数をチェック
    body_lines = [
        l for l in lines
        if l.strip() and not l.strip().startswith("■") and not l.strip().startswith("#")
    ]
    body_text = "".join(body_lines)
    body_len = len(body_text)
    if body_len < min_chars - 30 or body_len > max_chars + 30:
        return False, f"本文文字数が{body_len}文字です({min_chars}〜{max_chars}文字程度が目安)"

    return True, "OK"


def main():
    if len(sys.argv) < 2:
        print("使い方: python generate_tiktok_engagement.py <account_config.yaml>")
        sys.exit(1)

    config = load_account_config(sys.argv[1])

    system_prompt = load_system_prompt(config["prompt_file"])
    recent_titles = load_recent_titles(
        config["titles_log_file"], config.get("recent_titles_count", 30)
    )
    user_message = build_user_message(recent_titles)

    min_chars = config.get("min_chars", 200)
    max_chars = config.get("max_chars", 260)

    max_retries = 8
    output_text = ""
    for attempt in range(1, max_retries + 1):
        output_text = call_claude(system_prompt, user_message, config["model"])
        # コードフェンス(```)で囲まれてしまった場合の除去
        output_text = output_text.strip()
        if output_text.startswith("```"):
            output_text = re.sub(r"^```[a-zA-Z]*\n?", "", output_text)
            output_text = re.sub(r"\n?```$", "", output_text)
            output_text = output_text.strip()

        lines = output_text.split("\n")
        if lines:
            lines[0] = lines[0].replace("[", "").replace("]", "")
        output_text = "\n".join(lines)

        is_valid, msg = validate_output(output_text, min_chars, max_chars)
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

    # ハッシュタグ行とタイトル再掲行の間に空白行があると、画像化ツール側で
    # タイトル再掲を正しく除去できないため、直後に詰める
    lines = output_text.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip().startswith("#"):
            # このハッシュタグ行の後ろにある空行を削除する
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                del lines[j]
            break
    output_text = "\n".join(lines)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output_text)

    title = extract_title(output_text)
    append_title_log(config["titles_log_file"], title)

    print(f"生成完了: {output_path}")
    print(f"タイトル: {title}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"output_path={output_path}\n")


if __name__ == "__main__":
    main()
