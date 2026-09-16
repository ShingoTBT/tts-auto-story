import os
import requests

ZERNIO_API_BASE = "https://zernio.com/api/v1"
api_key = os.environ["ZERNIO_API_KEY_ACCOUNT5"]
target_id = os.environ["ZERNIO_TIKTOK_ACCOUNT_ID_6"]

headers = {"Authorization": f"Bearer {api_key}"}
r = requests.get(f"{ZERNIO_API_BASE}/accounts", headers=headers, timeout=20)
r.raise_for_status()
data = r.json()

accounts = data.get("accounts", data if isinstance(data, list) else [])

result_lines = []
found = False
for acc in accounts:
    acc_id = acc.get("_id") or acc.get("id")
    result_lines.append(f"id={acc_id}, platform={acc.get('platform')}, username={acc.get('username')}")
    if acc_id == target_id:
        found = True
        result_lines.append(f"  -> ★これが指定されたID({target_id})と一致")

result_lines.append(f"\n一致するアカウントが見つかったか: {found}")

output = "\n".join(result_lines)
print(output)
with open("diagnostics/account6_verify_result.txt", "w", encoding="utf-8") as f:
    f.write(output)
