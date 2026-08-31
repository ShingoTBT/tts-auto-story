#!/usr/bin/env python3
"""
全アカウントについて、「実際に最後に投稿が成功した時刻」を確認し、
想定より間隔が空きすぎている場合はChatWorkに通知する。

GitHub Actionsのワークフロー実行結果(success/failure)だけでは、
「ステップはskippedされ続けているのに、job全体はsuccess扱い」という
ケースを見逃してしまうため、実際に生成・投稿されたファイルの
タイムスタンプを直接確認する。

使い方:
    python monitor_accounts.py accounts/account1_emotional.yaml accounts/account2_news.yaml ...
"""

import sys
import os
import json
import datetime
from pathlib import Path

import requests
import yaml

CHATWORK_API_BASE = "https://api.chatwork.com/v2"


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_last_post_time_tiktok(config: dict):
    """TikTok系アカウントの、最後に生成されたテキストファイルの時刻を取得する"""
    output_dir = Path(config["output_dir"])
    if not output_dir.exists():
        return None

    timestamps = []
    for f in output_dir.glob("*.txt"):
        stem = f.stem
        # "20260826_164517" のようなファイル名からタイムスタンプを取り出す
        # (debug_failed_ や preview_ 等のプレフィックス付きファイルは除外)
        if not stem[:8].isdigit():
            continue
        try:
            dt = datetime.datetime.strptime(stem, "%Y%m%d_%H%M%S")
            timestamps.append(dt)
        except ValueError:
            continue

    if not timestamps:
        return None
    return max(timestamps)


def get_last_post_time_threads(config: dict):
    """Threads系アカウントの、posted_threads.jsonlの最終投稿時刻を取得する"""
    log_path = Path(config["output_dir"]) / "posted_threads.jsonl"
    if not log_path.exists():
        return None

    last_dt = None
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                dt = datetime.datetime.fromisoformat(record["posted_at"])
                if last_dt is None or dt > last_dt:
                    last_dt = dt
            except Exception:
                continue
    return last_dt


def send_chatwork_alert(token: str, room_id: str, body: str) -> None:
    r = requests.post(
        f"{CHATWORK_API_BASE}/rooms/{room_id}/messages",
        headers={"X-ChatWorkToken": token},
        data={"body": body},
        timeout=15,
    )
    r.raise_for_status()


def main():
    if len(sys.argv) < 2:
        print("使い方: python monitor_accounts.py <account_config1.yaml> [account_config2.yaml ...]")
        sys.exit(1)

    now = datetime.datetime.now()
    stalled = []
    ok_accounts = []

    for config_path in sys.argv[1:]:
        config = load_account_config(config_path)
        label = config.get("chatwork_label", config.get("display_name", config["account_name"]))
        platform = config.get("monitor_platform", "tiktok")
        max_hours = config.get("monitor_max_interval_hours", 8)

        if platform == "threads":
            last_time = get_last_post_time_threads(config)
        else:
            last_time = get_last_post_time_tiktok(config)

        if last_time is None:
            stalled.append((label, "投稿記録が一件も見つかりません"))
            print(f"{label}: 投稿記録なし")
            continue

        elapsed_hours = (now - last_time).total_seconds() / 3600
        print(f"{label}: 最終投稿={last_time.isoformat()} ({elapsed_hours:.1f}時間前, 基準={max_hours}h)")

        if elapsed_hours > max_hours:
            stalled.append((label, f"最終投稿から{elapsed_hours:.1f}時間経過(基準{max_hours}時間)"))
        else:
            ok_accounts.append(label)

    if stalled:
        lines = ["＝＝＝＝＝⚠️投稿停滞アラート＝＝＝＝＝"]
        lines.append(f"{now.strftime('%Y年%m月%d日 %H:%M')}時点")
        lines.append("")
        lines.append("以下のアカウントで、想定より投稿間隔が空いています。")
        for label, reason in stalled:
            lines.append(f"・{label}：{reason}")
        lines.append("")
        lines.append("ワークフローの実行履歴を確認してください。")
        lines.append("＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝")
        message = "\n".join(lines)

        cw_token = os.environ["CHATWORK_API_TOKEN"]
        cw_room = os.environ["CHATWORK_ROOM_ID"]
        send_chatwork_alert(cw_token, cw_room, message)
        print("\nアラートを送信しました:")
        print(message)
    else:
        print(f"\n全アカウント正常です({', '.join(ok_accounts)})")


if __name__ == "__main__":
    main()
