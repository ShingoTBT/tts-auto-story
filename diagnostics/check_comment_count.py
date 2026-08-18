#!/usr/bin/env python3
import os
import sys
import requests

ZERNIO_API_BASE = "https://zernio.com/api/v1"

api_key = os.environ["ZERNIO_API_KEY_ACCOUNT3"]
post_id = sys.argv[1]

headers = {"Authorization": f"Bearer {api_key}"}
account_id = os.environ["ZERNIO_THREADS_ACCOUNT_ID"]

# platformPostId(Metaネイティブ)とZernio内部_idの両方を試す
candidates = {
    "platformPostId": post_id,
}
zernio_internal_id = os.environ.get("ZERNIO_INTERNAL_ID")
if zernio_internal_id:
    candidates["zernio_internal_id"] = zernio_internal_id

results = []
for label, pid in candidates.items():
    r = requests.get(
        f"{ZERNIO_API_BASE}/inbox/comments/{pid}",
        headers=headers,
        params={"accountId": account_id},
        timeout=20,
    )
    results.append(f"[{label}={pid}] status: {r.status_code}\nbody: {r.text[:1000]}")

result = "\n\n".join(results)
print(result)

with open("diagnostics/comment_count_result.txt", "w", encoding="utf-8") as f:
    f.write(result)
