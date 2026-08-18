#!/usr/bin/env python3
"""
複数アカウント分のフォロワー数をZernio APIから取得し、
指定テンプレートでまとめてChatWorkに報告する。

使い方:
    python report_followers.py accounts/account1_emotional.yaml accounts/account2_news.yaml ...

必要な環境変数:
    ZERNIO_API_KEY
    (各アカウントのzernio_account_id_envで指定された環境変数。未接続なら省略可)
    CHATWORK_API_TOKEN
    CHATWORK_ROOM_ID
"""

import sys
import os
import datetime
from pathlib import Path

import yaml
import requests

ZERNIO_API_BASE = "https://zernio.com/api/v1"
CHATWORK_API_BASE = "https://api.chatwork.com/v2"
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_follower_count(api_key: str, account_id: str):
    headers = {"Authorization": f"Bearer {api_key}"}
    today = datetime.date.today().isoformat()
    params = {
        "accountIds": account_id,
        "fromDate": today,
        "toDate": today,
        "granularity": "daily",
    }
    r = requests.get(
        f"{ZERNIO_API_BASE}/accounts/follower-stats",
        headers=headers,
        params=params,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    accounts = data.get("accounts", [])
    if accounts:
        return accounts[0].get("currentFollowers")
    return None


def send_chatwork_message(token: str, room_id: str, body: str) -> None:
    r = requests.post(
        f"{CHATWORK_API_BASE}/rooms/{room_id}/messages",
        headers={"X-ChatWorkToken": token},
        data={"body": body},
        timeout=15,
    )
    r.raise_for_status()


def main():
    if len(sys.argv) < 2:
        print("使い方: python report_followers.py <account_config1.yaml> [account_config2.yaml ...]")
        sys.exit(1)

    zernio_key = os.environ.get("ZERNIO_API_KEY")
    cw_token = os.environ["CHATWORK_API_TOKEN"]
    cw_room = os.environ["CHATWORK_ROOM_ID"]

    lines = []
    for config_path in sys.argv[1:]:
        config = load_account_config(config_path)
        label = config.get("chatwork_label", config.get("display_name", config["account_name"]))
        account_id_env = config.get("zernio_account_id_env")
        account_id = os.environ.get(account_id_env) if account_id_env else None

        if not account_id or not zernio_key:
            lines.append(f"・{label}：未接続")
            continue

        try:
            count = get_follower_count(zernio_key, account_id)
            if count is None:
                lines.append(f"・{label}：取得失敗")
            else:
                lines.append(f"・{label}：{count}人")
        except Exception as e:
            print(f"エラー({label}): {e}")
            lines.append(f"・{label}：取得エラー")

    now_jst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    weekday_ja = WEEKDAY_JA[now_jst.weekday()]
    timestamp_str = f"{now_jst.year}年{now_jst.month:02d}月{now_jst.day:02d}日({weekday_ja}) {now_jst.hour:02d}:{now_jst.minute:02d}現在の総フォロワー数"

    message = (
        "＝＝＝＝＝フォロワーレポートです＝＝＝＝＝\n"
        f"{timestamp_str}\n"
        + "\n".join(lines) + "\n"
        "＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝"
    )

    send_chatwork_message(cw_token, cw_room, message)
    print("ChatWorkに報告しました")
    print(message)


if __name__ == "__main__":
    main()
