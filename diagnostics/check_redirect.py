#!/usr/bin/env python3
import os
import requests

redirect_url = os.environ["LINK_REDIRECT_URL"]
redirect_secret = os.environ["LINK_REDIRECT_SECRET"]
target = "https://item.rakuten.co.jp/"

results = []

# 1. 通常のユーザーとしてアクセス(リダイレクトを追わない)
r1 = requests.get(
    redirect_url,
    params={"secret": redirect_secret, "url": target},
    headers={"User-Agent": "Mozilla/5.0 (normal browser test)"},
    allow_redirects=False,
    timeout=15,
)
results.append(f"[通常ユーザー] status={r1.status_code}, Location={r1.headers.get('Location')}")

# 2. Metaのプレビュー生成クローラーとしてアクセス
r2 = requests.get(
    redirect_url,
    params={"secret": redirect_secret, "url": target},
    headers={"User-Agent": "facebookexternalhit/1.1"},
    allow_redirects=False,
    timeout=15,
)
results.append(f"[facebookexternalhit] status={r2.status_code}, body={r2.text[:200]}")

result = "\n".join(results)
print(result)

with open("diagnostics/redirect_check_result.txt", "w", encoding="utf-8") as f:
    f.write(result)
