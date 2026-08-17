#!/usr/bin/env python3
"""
Zernio APIから最新のTikTokフォロワー数を取得し、ChatWorkに報告する。

使い方:
    python report_followers.py accounts/account1_emotional.yaml

必要な環境変数:
    ZERNIO_API_KEY
    ZERNIO_TIKTOK_ACCOUNT_ID
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


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_follower_count(api_key: str, account_id: str) -> dict:
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
    return r.json()


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
        print("使い方: python report_followers.py <account_config.yaml>")
        sys.exit(1)

    config = load_account_config(sys.argv[1])

    zernio_key = os.environ["ZERNIO_API_KEY"]
    account_id = os.environ["ZERNIO_TIKTOK_ACCOUNT_ID"]
    cw_token = os.environ["CHATWORK_API_TOKEN"]
    cw_room = os.environ["CHATWORK_ROOM_ID"]

    data = get_follower_count(zernio_key, account_id)
    print("Zernio follower-stats response:", data)

    # レスポンス構造からフォロワー数を抽出(構造は実際のレスポンスを見て調整)
    follower_count = None
    try:
        accounts = data.get("accounts") or data.get("data") or []
        if accounts:
            entry = accounts[0]
            follower_count = (
                entry.get("followerCount")
                or entry.get("latestFollowerCount")
                or entry.get("count")
            )
    except Exception as e:
        print("パース時に問題がありました:", e)

    today_str = datetime.date.today().strftime("%Y年%m月%d日")

    if follower_count is not None:
        message = f"【フォロワー数レポート】{today_str}\n{follower_count}人"
    else:
        message = (
            f"【フォロワー数レポート】{today_str}\n"
            f"取得はできましたが、件数の抽出に失敗しました。生データ: {data}"
        )

    send_chatwork_message(cw_token, cw_room, message)
    print("ChatWorkに報告しました")


if __name__ == "__main__":
    main()
