#!/usr/bin/env python3
"""
生成されたストーリーのタイトル・キャプションと、画像化された3枚のPNGを
ChatWorkの指定ルームに自動送信する。

使い方:
    python notify_chatwork.py accounts/account1_emotional.yaml <source_text_file.txt>

必要な環境変数:
    CHATWORK_API_TOKEN
    CHATWORK_ROOM_ID
"""

import sys
import os
from pathlib import Path

import yaml
import requests

API_BASE = "https://api.chatwork.com/v2"


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def send_message(token: str, room_id: str, body: str) -> None:
    r = requests.post(
        f"{API_BASE}/rooms/{room_id}/messages",
        headers={"X-ChatWorkToken": token},
        data={"body": body},
        timeout=15,
    )
    r.raise_for_status()


def send_file(token: str, room_id: str, file_path: Path, message: str = "") -> None:
    with open(file_path, "rb") as f:
        r = requests.post(
            f"{API_BASE}/rooms/{room_id}/files",
            headers={"X-ChatWorkToken": token},
            files={"file": (file_path.name, f, "image/png")},
            data={"message": message},
            timeout=30,
        )
    r.raise_for_status()


def main():
    if len(sys.argv) < 3:
        print("使い方: python notify_chatwork.py <account_config.yaml> <source_text_file.txt>")
        sys.exit(1)

    config_path = sys.argv[1]
    source_text_path = Path(sys.argv[2])

    config = load_account_config(config_path)

    token = os.environ["CHATWORK_API_TOKEN"]
    room_id = os.environ["CHATWORK_ROOM_ID"]

    with open(source_text_path, "r", encoding="utf-8") as f:
        source_text = f.read()

    title = source_text.split("\n", 1)[0].strip()

    display_name = config.get("display_name", config["account_name"])
    chatwork_label = config.get("chatwork_label", display_name)
    tiktok_url = config.get("tiktok_url", "")

    # 1. 投稿完了報告(指定テンプレート)
    message_body = (
        f"[info]投稿通知：{chatwork_label}\n"
        f"{tiktok_url}[hr]{source_text}[/info]"
    )
    send_message(token, room_id, message_body)

    print("ChatWorkに通知しました")


if __name__ == "__main__":
    main()
