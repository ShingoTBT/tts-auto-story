import os
import requests

ZERNIO_API_BASE = "https://zernio.com/api/v1"
api_key = os.environ["ZERNIO_API_KEY_ACCOUNT5"]
account_id = os.environ["ZERNIO_TIKTOK_ACCOUNT_ID_6"]

headers = {"Authorization": f"Bearer {api_key}"}

result_lines = []

# 投稿一覧を確認(アカウント指定・直近分)
try:
    r = requests.get(
        f"{ZERNIO_API_BASE}/posts",
        headers=headers,
        params={"accountId": account_id, "limit": 5},
        timeout=20,
    )
    result_lines.append(f"GET /posts status={r.status_code}")
    result_lines.append(r.text[:3000])
except Exception as e:
    result_lines.append(f"GET /posts エラー: {e}")

output = "\n".join(result_lines)
print(output)
with open("diagnostics/account6_post_check.txt", "w", encoding="utf-8") as f:
    f.write(output)
