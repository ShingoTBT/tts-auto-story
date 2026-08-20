import os
import requests

ZERNIO_API_BASE = "https://zernio.com/api/v1"
api_key = os.environ["ZERNIO_API_KEY_ACCOUNT3"]
account_id = os.environ["ZERNIO_THREADS_ACCOUNT_ID_2"]
post_id = "17884175382616984"

headers = {"Authorization": f"Bearer {api_key}"}
all_comments = []
cursor = None

while True:
    params = {"accountId": account_id}
    if cursor:
        params["cursor"] = cursor
    r = requests.get(f"{ZERNIO_API_BASE}/inbox/comments/{post_id}", headers=headers, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    comments = data.get("comments", [])
    all_comments.extend(comments)
    pagination = data.get("pagination", {})
    if pagination.get("hasMore") and pagination.get("cursor"):
        cursor = pagination["cursor"]
    else:
        break

result_lines = [f"総コメント数: {len(all_comments)}"]
our_comments = [c for c in all_comments if "ad" in str(c) or "kinoteck" in str(c) or "kinoteck" in str(c.get("text", ""))]
result_lines.append(f"自分(広告リンク付き)のコメント数: {len(our_comments)}")
for c in our_comments:
    result_lines.append(f" - id={c.get('id')} | created={c.get('timestamp') or c.get('createdAt')} | text={str(c.get('text'))[:100]}")

result = "\n".join(result_lines)
print(result)
with open("diagnostics/duplicate_check_result.txt", "w", encoding="utf-8") as f:
    f.write(result)
