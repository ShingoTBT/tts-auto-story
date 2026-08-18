#!/usr/bin/env python3
"""
生成されたテキストをZernio API経由でThreadsに投稿する(テキストのみ・画像なし)。
投稿後、postIdをログに保存し、後のコメント自動チェックで使う。

使い方:
    python post_to_threads.py accounts/account3_threads.yaml <source_text_file.txt>
"""

import sys
import os
import json
import datetime
from pathlib import Path

import yaml
import requests

ZERNIO_API_BASE = "https://zernio.com/api/v1"


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def create_post(api_key: str, account_id: str, text: str) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "content": text,
        "platforms": [
            {"platform": "threads", "accountId": account_id}
        ],
        "publishNow": True,
    }
    r = requests.post(f"{ZERNIO_API_BASE}/posts", headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def append_post_log(log_path: str, record: dict) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main():
    if len(sys.argv) < 3:
        print("使い方: python post_to_threads.py <account_config.yaml> <source_text_file.txt>")
        sys.exit(1)

    config_path = sys.argv[1]
    source_text_path = Path(sys.argv[2])

    config = load_account_config(config_path)
    api_key = os.environ[config.get("zernio_api_key_env", "ZERNIO_API_KEY")]
    account_id = os.environ[config["zernio_account_id_env"]]

    text = source_text_path.read_text(encoding="utf-8")

    result = create_post(api_key, account_id, text)
    print("Zernio投稿結果:", result)

    # postIdを抽出して記録(コメント自動チェック用)
    post_id = None
    try:
        platform_results = result.get("post", {}).get("platformResults", []) or result.get("platformResults", [])
        for pr in platform_results:
            if pr.get("platform") == "threads":
                post_id = pr.get("postId") or pr.get("id")
    except Exception as e:
        print("postId抽出時に問題がありました:", e)

    log_dir = Path(config["output_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    posts_log_path = log_dir / "posted_threads.jsonl"

    record = {
        "post_id": post_id,
        "posted_at": datetime.datetime.now().isoformat(),
        "text_file": str(source_text_path),
        "commented": False,
        "raw_result": result,
    }
    append_post_log(posts_log_path, record)

    print(f"投稿ログに記録しました: {posts_log_path} (post_id={post_id})")


if __name__ == "__main__":
    main()
