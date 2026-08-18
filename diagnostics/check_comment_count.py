#!/usr/bin/env python3
import os
import sys
import requests

ZERNIO_API_BASE = "https://zernio.com/api/v1"

api_key = os.environ["ZERNIO_API_KEY_ACCOUNT3"]
post_id = sys.argv[1]

headers = {"Authorization": f"Bearer {api_key}"}
account_id = os.environ.get("ZERNIO_ACCOUNT_ID_OVERRIDE") or os.environ["ZERNIO_THREADS_ACCOUNT_ID"]

r = requests.get(
    f"{ZERNIO_API_BASE}/inbox/comments/{post_id}",
    headers=headers,
    params={"accountId": account_id},
    timeout=20,
)
result = f"status: {r.status_code}\nbody: {r.text}"
print(result[:3000])

with open("diagnostics/comment_count_result.txt", "w", encoding="utf-8") as f:
    f.write(result)
