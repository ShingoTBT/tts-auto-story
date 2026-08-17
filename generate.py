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


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
    if not recent_titles:
        return "新しい感動ストーリーを1本、指定フォーマットで生成してください。"

    titles_block = "\n".join(f"- {t}" for t in recent_titles)
    return (
        "新しい感動ストーリーを1本、指定フォーマットで生成してください。\n\n"
        "以下は直近に使用済みのタイトル一覧です。内容・切り口ともに重複や類似がないようにしてください。\n"
        f"{titles_block}"
    )


def call_claude(system_prompt: str, user_message: str, model: str) -> str:
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY環境変数を自動参照

    response = client.messages.create(
        model=model,
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

    separator_count = sum(1 for line in lines if re.match(r"^\s*={2,}\s*$", line))
    if separator_count < 2:
        return False, f"'==' 区切り行が{separator_count}個しかありません（3ブロックには2個必要）。"

    hashtag_lines = [line for line in lines if line.strip().startswith("#")]
    if not hashtag_lines:
        return False, "ハッシュタグ行(#始まり)が見つかりません。"

    return True, "OK"


def extract_title(text: str) -> str:
    lines = text.split("\n")
    return lines[0].strip() if lines else ""


def main():
    if len(sys.argv) < 2:
        print("使い方: python generate.py <account_config.yaml>")
        sys.exit(1)

    config_path = sys.argv[1]
    config = load_account_config(config_path)

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
        is_valid, message = validate_output_format(output_text)

        if is_valid:
            break

        print(f"[試行{attempt}] フォーマット不正: {message}")
        if attempt == max_retries:
            print("最大リトライ回数に達しました。生成を中止します。")
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

    # GitHub Actions側で後続ステップ(Puppeteer画像化・Slack通知)が
    # このファイルを参照できるよう、パスを標準出力に出しておく
    print(f"::set-output name=output_path::{output_path}")


if __name__ == "__main__":
    main()
