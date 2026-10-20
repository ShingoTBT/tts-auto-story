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


FAILURE_NOTICE_INTERVAL_HOURS = 6
USER_CHATWORK_TO = "[To:237628]"


def notify_post_failed(config: dict, source_text_path: Path, token: str, room_id: str) -> None:
    """投稿(Zernio)に失敗したときの知らせ。同じアカウントは6時間に1回まで(毎時の失敗で通知が埋まらないように)"""
    import json
    import datetime

    output_dir = Path(config["output_dir"])
    state_path = output_dir / "post_failure_notice.json"
    now = datetime.datetime.now()
    try:
        last = datetime.datetime.fromisoformat(json.loads(state_path.read_text(encoding="utf-8"))["last_notified_at"])
        if (now - last).total_seconds() / 3600 < FAILURE_NOTICE_INTERVAL_HOURS:
            print(f"直近{FAILURE_NOTICE_INTERVAL_HOURS}時間以内に失敗を知らせ済みのため、今回は送りません")
            return
    except Exception:
        pass

    detail = ""
    error_path = output_dir / f"zernio_error_{source_text_path.stem}.txt"
    if error_path.exists():
        lines = error_path.read_text(encoding="utf-8").splitlines()
        detail = "\n".join(l[:300] for l in lines if l.startswith(("status_code", "response_body")))

    display_name = config.get("display_name", config["account_name"])
    chatwork_label = config.get("chatwork_label", display_name)
    post_url = config.get("tiktok_url") or config.get("threads_url", "")
    body = (
        f"{USER_CHATWORK_TO}\n"
        f"[info][title]⚠️投稿失敗：{chatwork_label}[/title]"
        f"文章は作れましたが、Zernio への投稿が失敗し、SNS には載っていません。\n"
        f"{post_url}\n"
        f"（同じアカウントの失敗の知らせは{FAILURE_NOTICE_INTERVAL_HOURS}時間に1回までにしています）"
        + (f"[hr]{detail}" if detail else "")
        + "[/info]"
    )
    send_message(token, room_id, body)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_notified_at": now.isoformat()}), encoding="utf-8")
    print("ChatWorkに投稿失敗を通知しました")


def main():
    if len(sys.argv) < 3:
        print("使い方: python notify_chatwork.py <account_config.yaml> <source_text_file.txt> [--failed]")
        sys.exit(1)

    if "--failed" in sys.argv[3:]:
        config = load_account_config(sys.argv[1])
        notify_post_failed(config, Path(sys.argv[2]), os.environ["CHATWORK_API_TOKEN"], os.environ["CHATWORK_ROOM_ID"])
        return

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
    post_url = config.get("tiktok_url") or config.get("threads_url", "")

    # 1. 投稿完了報告(指定テンプレート)
    message_body = (
        f"[info]投稿通知：{chatwork_label}\n"
        f"{post_url}[hr]{source_text}[/info]"
    )
    send_message(token, room_id, message_body)

    print("ChatWorkに通知しました")


if __name__ == "__main__":
    main()
