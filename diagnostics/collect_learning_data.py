#!/usr/bin/env python3
"""
account4(kusetsuyo.qa)の全投稿について、投稿本文と、
アカウント本人が付けたコメント(商品リンク付きのものを含む)を全件収集する。
学習・分析用。
"""

import os
import json
import re
import sys
from pathlib import Path

import requests

# ルート直下のモジュールをインポートできるようにパスを追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from check_and_reply_comments import load_account_config, _extract_own_username

ZERNIO_API_BASE = "https://zernio.com/api/v1"


def fetch_all_comments(api_key: str, post_id: str, account_id: str, max_pages: int = 20) -> list[dict]:
    headers = {"Authorization": f"Bearer {api_key}"}
    all_comments = []
    cursor = None
    pages = 0

    while True:
        params = {"accountId": account_id}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{ZERNIO_API_BASE}/inbox/comments/{post_id}", headers=headers, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        comments = data.get("comments", data.get("data", []))
        all_comments.extend(comments)
        pages += 1

        pagination = data.get("pagination", {})
        if pages >= max_pages:
            break
        if pagination.get("hasMore") and pagination.get("cursor"):
            cursor = pagination["cursor"]
        else:
            break

    return all_comments


def main():
    try:
        _run()
    except Exception as e:
        import traceback
        crash_log = traceback.format_exc()
        print(crash_log)
        try:
            with open("outputs/account4_threads/learning_data_crash.txt", "w", encoding="utf-8") as f:
                f.write(crash_log)
        except Exception:
            pass
        raise


def _run():
    config = load_account_config("accounts/account4_threads_engagement.yaml")
    api_key = os.environ[config.get("zernio_api_key_env", "ZERNIO_API_KEY")]
    account_id = os.environ[config["zernio_account_id_env"]]
    own_username = _extract_own_username(config).lower()

    log_path = Path(config["output_dir"]) / "posted_threads.jsonl"
    records = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    results = []
    errors = []

    def save_progress():
        output_path = Path(config["output_dir"]) / "learning_data.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({"results": results, "errors": errors, "total_records": len(records)}, f, ensure_ascii=False, indent=2)
        return output_path

    for record in records:
        post_id = record.get("post_id")
        if not post_id:
            continue

        text_file = record.get("text_file")
        post_text = ""
        if text_file and Path(text_file).exists():
            post_text = Path(text_file).read_text(encoding="utf-8")

        try:
            comments = fetch_all_comments(api_key, post_id, account_id)
        except Exception as e:
            print(f"エラー (post_id={post_id}): {e}")
            errors.append(f"post_id={post_id}: {e}")
            save_progress()
            continue

        own_comments = []
        for c in comments:
            author = str(
                c.get("username") or c.get("from", {}).get("username", "") or c.get("authorUsername", "")
            ).lstrip("@").lower()
            if author == own_username:
                text = c.get("text") or c.get("message") or ""
                own_comments.append(text)

        if own_comments:
            results.append({
                "post_id": post_id,
                "post_text": post_text,
                "own_comments": own_comments,
            })
            print(f"post_id={post_id}: 自分のコメント{len(own_comments)}件を発見")
        else:
            print(f"post_id={post_id}: 自分のコメントなし")

        save_progress()

    output_path = save_progress()
    print(f"\n完了: {len(results)}件の投稿にコメントあり, エラー{len(errors)}件 -> {output_path}")


if __name__ == "__main__":
    main()
