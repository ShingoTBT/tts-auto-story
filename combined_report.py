#!/usr/bin/env python3
"""
TikTokアカウントのフォロワー数レポートと、Threadsアカウントの
「コメント20件超え投稿数」レポートを、それぞれChatWorkに送信する。

使い方:
    python combined_report.py --tiktok accounts/account1_emotional.yaml accounts/account2_news.yaml accounts/account5_tiktok_engagement.yaml --threads accounts/account3_threads.yaml accounts/account4_threads_engagement.yaml
"""

import sys
import os
import json
import datetime
from pathlib import Path

import requests

from report_followers import load_account_config, get_follower_count, get_follower_count_with_delta
from rootbuzz_report import build_rootbuzz_report
from check_and_reply_comments import (
    load_posted_threads,
    is_within_check_window,
    get_comment_count,
)

WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]
CHATWORK_API_BASE = "https://api.chatwork.com/v2"


def now_jst_str() -> tuple[str, datetime.datetime]:
    now_jst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    weekday_ja = WEEKDAY_JA[now_jst.weekday()]
    timestamp_str = f"{now_jst.year}年{now_jst.month:02d}月{now_jst.day:02d}日({weekday_ja}) {now_jst.hour:02d}:{now_jst.minute:02d}"
    return timestamp_str, now_jst


def send_chatwork_message(token: str, room_id: str, body: str) -> None:
    r = requests.post(
        f"{CHATWORK_API_BASE}/rooms/{room_id}/messages",
        headers={"X-ChatWorkToken": token},
        data={"body": body},
        timeout=15,
    )
    r.raise_for_status()


def build_follower_report(tiktok_configs: list[str], timestamp_str: str) -> str:
    zernio_key_default = os.environ.get("ZERNIO_API_KEY")
    lines = []

    for config_path in tiktok_configs:
        config = load_account_config(config_path)
        label = config.get("chatwork_label", config.get("display_name", config["account_name"]))
        api_key_env = config.get("zernio_api_key_env", "ZERNIO_API_KEY")
        account_id_env = config.get("zernio_account_id_env")
        api_key = os.environ.get(api_key_env) or zernio_key_default
        account_id = os.environ.get(account_id_env) if account_id_env else None

        if not account_id or not api_key:
            lines.append(f"・{label}：未接続")
            continue

        try:
            count, delta = get_follower_count_with_delta(api_key, account_id)
            if count is None:
                lines.append(f"・{label}：取得失敗")
            elif delta is None:
                lines.append(f"・{label}：{count}人")
            else:
                sign = "+" if delta >= 0 else ""
                lines.append(f"・{label}：{count}人【前日{sign}{delta}】")
        except Exception as e:
            print(f"エラー({label}): {e}")
            lines.append(f"・{label}：取得エラー")

    voice_lines = build_voice_lines(tiktok_configs)
    return (
        "＝＝＝＝＝フォロワーレポートです＝＝＝＝＝\n"
        f"{timestamp_str}現在\n"
        "■TikTokの総フォロワー数\n"
        + "\n".join(lines) + "\n"
        + ("\n".join(voice_lines) + "\n" if voice_lines else "")
        + "＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝"
    )


def build_voice_lines(tiktok_configs: list[str]) -> list[str]:
    """動画で投稿しているアカウントの、昨日(日本時間)の投稿に使った声(ElevenLabs か VOICEVOX)の本数と、
    ElevenLabs の残り文字数(最後の投稿のときの値)。2026-10-23〜"""
    yesterday = (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).date() - datetime.timedelta(days=1)
    lines = []
    latest = None  # (posted_at, remaining)
    for config_path in tiktok_configs:
        config = load_account_config(config_path)
        if "video" not in config:
            continue
        label = config.get("chatwork_label", config.get("display_name", config["account_name"]))
        counts = {}
        log = Path(config["output_dir"]) / "posted_videos.jsonl"
        if log.exists():
            for raw in log.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                at = rec.get("posted_at", "")
                if rec.get("elevenlabs_remaining") is not None and (latest is None or at > latest[0]):
                    latest = (at, rec["elevenlabs_remaining"])
                if at[:10] == yesterday.isoformat():
                    eng = rec.get("voice_engine") or "不明"
                    counts[eng] = counts.get(eng, 0) + 1
        detail = "・".join(f"{k} {v}本" for k, v in sorted(counts.items())) or "投稿なし"
        lines.append(f"・{label}：{detail}")
    if not lines:
        return []
    out = [f"■動画の声（昨日{yesterday.month}/{yesterday.day}の投稿）"] + lines
    if latest:
        out.append(f"・ElevenLabs の残り：{latest[1]:,}字（足りなくなると自動で VOICEVOX に切り替え）")
    return out


def build_comment_report(threads_configs: list[str], timestamp_str: str) -> str:
    lines = []

    for config_path in threads_configs:
        config = load_account_config(config_path)
        label = config.get("chatwork_label", config.get("display_name", config["account_name"]))
        api_key = os.environ.get(config.get("zernio_api_key_env", "ZERNIO_API_KEY"))
        account_id = os.environ.get(config["zernio_account_id_env"])
        threshold = config.get("comment_threshold", 20)
        check_days = config.get("comment_check_days", 2)

        log_path = Path(config["output_dir"]) / "posted_threads.jsonl"
        records = load_posted_threads(str(log_path))

        if not api_key or not account_id:
            lines.append(f"・{label}：未接続")
            continue

        over_count = 0
        for record in records:
            post_id = record.get("post_id")
            if not post_id:
                continue
            if not is_within_check_window(record["posted_at"], check_days):
                continue
            try:
                count = get_comment_count(api_key, post_id, account_id)
            except Exception as e:
                print(f"エラー({label}, post_id={post_id}): {e}")
                continue
            if count > threshold:
                over_count += 1

        lines.append(f"・{label}：{over_count}投稿")

    return (
        "＝＝＝＝＝コメント数レポートです＝＝＝＝＝\n"
        f"{timestamp_str}現在\n"
        "■Threadsで20コメントを超えている数（昨日〜本日現時点）\n"
        + "\n".join(lines) + "\n"
        "＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝"
    )


def main():
    if "--tiktok" not in sys.argv or "--threads" not in sys.argv:
        print("使い方: python combined_report.py --tiktok <config...> --threads <config...> [--rootbuzz <config...>]")
        sys.exit(1)

    # --rootbuzz <config...> は先に取り出す(次の -- まで。残りは今までどおりの解釈)
    args = list(sys.argv)
    rootbuzz_configs = []
    if "--rootbuzz" in args:
        i = args.index("--rootbuzz")
        j = i + 1
        while j < len(args) and not args[j].startswith("--"):
            j += 1
        rootbuzz_configs = args[i + 1:j]
        del args[i:j]

    tiktok_idx = args.index("--tiktok")
    threads_idx = args.index("--threads")

    if tiktok_idx < threads_idx:
        tiktok_configs = args[tiktok_idx + 1:threads_idx]
        threads_configs = args[threads_idx + 1:]
    else:
        threads_configs = args[threads_idx + 1:tiktok_idx]
        tiktok_configs = args[tiktok_idx + 1:]

    cw_token = os.environ["CHATWORK_API_TOKEN"]
    cw_room = os.environ["CHATWORK_ROOM_ID"]

    timestamp_str, _ = now_jst_str()

    follower_report = build_follower_report(tiktok_configs, timestamp_str)
    print(follower_report)
    send_chatwork_message(cw_token, cw_room, follower_report)

    comment_report = build_comment_report(threads_configs, timestamp_str)
    print(comment_report)
    send_chatwork_message(cw_token, cw_room, comment_report)

    if rootbuzz_configs:
        # ルートバズのレポートで何かあっても、上の2つ(本来のレポート)には影響させない
        try:
            rootbuzz_report = build_rootbuzz_report(rootbuzz_configs, timestamp_str)
            print(rootbuzz_report)
            send_chatwork_message(cw_token, cw_room, rootbuzz_report)
        except Exception as e:
            print(f"エラー(ルートバズレポート): {e}")


if __name__ == "__main__":
    main()
