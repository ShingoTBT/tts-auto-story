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
GITHUB_API_BASE = "https://api.github.com"
GITHUB_OWNER = "ShingoTBT"
GITHUB_REPO = "tts-auto-story"
RECOVERY_STATE_PATH = Path("outputs/monitor_recovery_state.json")
RECOVERY_COOLDOWN_HOURS = 4  # 同じアカウントへの自動再実行は、この時間は連続で行わない
CHATWORK_MY_ACCOUNT_ID = "237628"  # しんさんのChatWorkアカウントID(TO付き緊急通知用)

# クレジット枯渇を検知するための、生成スクリプトのクラッシュログファイル一覧
CRASH_LOG_FILES = [
    "diagnostics/generate_tiktok_engagement_crash.txt",
    "diagnostics/generate_threads_engagement_crash.txt",
    "diagnostics/generate_threads_post_crash.txt",
    "diagnostics/generate_news_crash.txt",
]
CREDIT_ERROR_MARKER = "credit balance is too low"
CREDIT_ALERT_STATE_PATH = Path("outputs/monitor_credit_alert_state.json")
CREDIT_ALERT_COOLDOWN_HOURS = 3  # クレジット枯渇の再通知間隔(直しても直後に別の失敗で連打しないため)


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


def load_recovery_state() -> dict:
    if not RECOVERY_STATE_PATH.exists():
        return {}
    try:
        with open(RECOVERY_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_recovery_state(state: dict) -> None:
    RECOVERY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RECOVERY_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def can_attempt_recovery(state: dict, account_name: str) -> bool:
    last_attempt_str = state.get(account_name)
    if not last_attempt_str:
        return True
    last_attempt = datetime.datetime.fromisoformat(last_attempt_str)
    elapsed_hours = (datetime.datetime.now() - last_attempt).total_seconds() / 3600
    return elapsed_hours >= RECOVERY_COOLDOWN_HOURS


def trigger_workflow_dispatch(dispatch_token: str, workflow_file: str) -> bool:
    """指定したワークフローをworkflow_dispatchで再実行する"""
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/{workflow_file}/dispatches"
    headers = {
        "Authorization": f"Bearer {dispatch_token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        r = requests.post(url, headers=headers, json={"ref": "main"}, timeout=20)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"  再実行トリガーエラー: {e}")
        return False


def check_credit_exhaustion() -> bool:
    """
    生成スクリプトのクラッシュログに、Anthropic APIのクレジット枯渇エラーが
    含まれていないか確認する。
    """
    for path in CRASH_LOG_FILES:
        p = Path(path)
        if not p.exists():
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if CREDIT_ERROR_MARKER in content:
            return True
    return False


def load_credit_alert_state() -> dict:
    if not CREDIT_ALERT_STATE_PATH.exists():
        return {}
    try:
        with open(CREDIT_ALERT_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_credit_alert_state(state: dict) -> None:
    CREDIT_ALERT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CREDIT_ALERT_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def send_credit_exhaustion_alert(token: str, room_id: str) -> None:
    """クレジット枯渇を検知した場合、TO付きの緊急通知を送る"""
    now = datetime.datetime.now()
    state = load_credit_alert_state()
    last_alert = state.get("last_alert")
    if last_alert:
        elapsed_hours = (now - datetime.datetime.fromisoformat(last_alert)).total_seconds() / 3600
        if elapsed_hours < CREDIT_ALERT_COOLDOWN_HOURS:
            print(f"クレジット枯渇アラートはクールダウン中です(あと約{CREDIT_ALERT_COOLDOWN_HOURS - elapsed_hours:.1f}時間)")
            return

    message = (
        f"[To:{CHATWORK_MY_ACCOUNT_ID}]しんさん\n"
        "＝＝＝＝＝🚨緊急：Anthropic APIクレジット枯渇＝＝＝＝＝\n"
        f"{now.strftime('%Y年%m月%d日 %H:%M')}時点\n\n"
        "コンテンツ生成に使っているAnthropic APIのクレジット残高が不足し、\n"
        "全アカウントの投稿生成が停止しています。\n\n"
        "以下から至急クレジットを追加してください:\n"
        "https://console.anthropic.com/settings/billing\n\n"
        "追加が完了次第、教えてください。止まっていた分の投稿をまとめて実行します。\n"
        "＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝"
    )
    send_chatwork_alert(token, room_id, message)
    state["last_alert"] = now.isoformat()
    save_credit_alert_state(state)
    print("クレジット枯渇の緊急アラートを送信しました")


def main():
    if len(sys.argv) < 2:
        print("使い方: python monitor_accounts.py <account_config1.yaml> [account_config2.yaml ...]")
        sys.exit(1)

    now = datetime.datetime.now()
    stalled = []
    ok_accounts = []
    recovery_state = load_recovery_state()
    dispatch_token = os.environ.get("GH_DISPATCH_TOKEN")

    # クレジット枯渇の検知(最優先。検知したら即TO付き緊急通知)
    cw_token = os.environ["CHATWORK_API_TOKEN"]
    cw_room = os.environ["CHATWORK_ROOM_ID"]
    if check_credit_exhaustion():
        print("⚠️ Anthropic APIのクレジット枯渇を検知しました")
        send_credit_exhaustion_alert(cw_token, cw_room)

    for config_path in sys.argv[1:]:
        config = load_account_config(config_path)
        label = config.get("chatwork_label", config.get("display_name", config["account_name"]))
        account_name = config["account_name"]
        platform = config.get("monitor_platform", "tiktok")
        max_hours = config.get("monitor_max_interval_hours", 8)
        recovery_workflow = config.get("monitor_recovery_workflow")

        if platform == "threads":
            last_time = get_last_post_time_threads(config)
        else:
            last_time = get_last_post_time_tiktok(config)

        if last_time is None:
            elapsed_hours = None
            is_stalled = True
            reason = "投稿記録が一件も見つかりません"
            print(f"{label}: 投稿記録なし")
        else:
            elapsed_hours = (now - last_time).total_seconds() / 3600
            is_stalled = elapsed_hours > max_hours
            reason = f"最終投稿から{elapsed_hours:.1f}時間経過(基準{max_hours}時間)"
            print(f"{label}: 最終投稿={last_time.isoformat()} ({elapsed_hours:.1f}時間前, 基準={max_hours}h)")

        if not is_stalled:
            ok_accounts.append(label)
            continue

        # 自動復旧を試みる(クールダウン期間内は再試行しない)
        recovery_note = ""
        if recovery_workflow and dispatch_token:
            if can_attempt_recovery(recovery_state, account_name):
                print(f"  自動復旧を試みます: {recovery_workflow}")
                success = trigger_workflow_dispatch(dispatch_token, recovery_workflow)
                recovery_state[account_name] = now.isoformat()
                recovery_note = (
                    "自動で再実行をトリガーしました。数分後に結果をご確認ください。"
                    if success
                    else "自動再実行のトリガーに失敗しました。"
                )
            else:
                last_attempt = datetime.datetime.fromisoformat(recovery_state[account_name])
                cooldown_remaining = RECOVERY_COOLDOWN_HOURS - (now - last_attempt).total_seconds() / 3600
                recovery_note = (
                    f"直近{RECOVERY_COOLDOWN_HOURS}時間以内に自動再実行を試みたため、"
                    f"今回はスキップしました(あと約{cooldown_remaining:.1f}時間で再試行可能)。"
                )
        else:
            recovery_note = "自動復旧の設定がないため、手動確認が必要です。"

        stalled.append((label, reason, recovery_note))

    save_recovery_state(recovery_state)

    if stalled:
        lines = ["＝＝＝＝＝⚠️投稿停滞アラート＝＝＝＝＝"]
        lines.append(f"{now.strftime('%Y年%m月%d日 %H:%M')}時点")
        lines.append("")
        lines.append("以下のアカウントで、想定より投稿間隔が空いています。")
        for label, reason, recovery_note in stalled:
            lines.append(f"・{label}：{reason}")
            lines.append(f"　→ {recovery_note}")
        lines.append("")
        lines.append("自動再実行後もこの通知が続く場合は、コードの調査が必要な可能性があります。")
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
