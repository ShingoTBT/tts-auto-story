#!/usr/bin/env python3
"""
Zernio APIのOpenAPI仕様(あれば)やドキュメントページから、
コメント関連(特に「固定/pin」に相当する操作)のエンドポイントを確認する診断スクリプト。
"""

import os
import requests

API_KEY = os.environ["ZERNIO_API_KEY"]
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

candidate_urls = [
    "https://zernio.com/api/v1/openapi.json",
    "https://docs.zernio.com/openapi.json",
    "https://zernio.com/openapi.json",
]

for url in candidate_urls:
    try:
        r = requests.get(url, timeout=15)
        print(f"{url} -> status {r.status_code}, content-type: {r.headers.get('content-type')}")
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            data = r.json()
            paths = data.get("paths", {})
            print(f"  {len(paths)}個のエンドポイントを発見")
            pin_related = [p for p in paths if "pin" in p.lower()]
            comment_related = [p for p in paths if "comment" in p.lower() or "repl" in p.lower()]
            print("  'pin'を含むパス:", pin_related)
            print("  'comment'/'reply'を含むパス:", comment_related)
    except Exception as e:
        print(f"{url} -> エラー: {e}")

# ドキュメントページのテキストからも"pin"の言及を探す
doc_urls = [
    "https://docs.zernio.com/platforms/threads",
    "https://docs.zernio.com/comments",
]
for url in doc_urls:
    try:
        r = requests.get(url, timeout=15)
        print(f"\n{url} -> status {r.status_code}")
        if r.status_code == 200:
            text = r.text
            if "pin" in text.lower():
                idx = text.lower().find("pin")
                print("  'pin'の記述を発見:", text[max(0, idx-100):idx+200])
            else:
                print("  'pin'の記述は見つかりませんでした")
    except Exception as e:
        print(f"{url} -> エラー: {e}")
