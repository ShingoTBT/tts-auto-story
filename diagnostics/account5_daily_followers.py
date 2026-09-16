import os
import requests

ZERNIO_API_BASE = "https://zernio.com/api/v1"

api_key = os.environ["ZERNIO_API_KEY_ACCOUNT5"]
account_id = os.environ["ZERNIO_TIKTOK_ACCOUNT_ID_3"]

headers = {"Authorization": f"Bearer {api_key}"}
params = {
    "accountIds": account_id,
    "fromDate": "2026-08-18",
    "toDate": "2026-09-15",
    "granularity": "daily",
}
r = requests.get(f"{ZERNIO_API_BASE}/accounts/follower-stats", headers=headers, params=params, timeout=30)
r.raise_for_status()
data = r.json()

stats = data.get("stats", {})
daily = stats.get(account_id, [])
daily_sorted = sorted(daily, key=lambda x: x["date"])

lines = []
prev = None
for entry in daily_sorted:
    date = entry["date"]
    followers = entry["followers"]
    if prev is None:
        delta_str = "-"
    else:
        delta = followers - prev
        sign = "+" if delta >= 0 else ""
        delta_str = f"{sign}{delta}"
    lines.append(f"{date}: {followers}人 (前日比 {delta_str})")
    prev = followers

output = "\n".join(lines)
print(output)
with open("diagnostics/account5_daily_followers.txt", "w", encoding="utf-8") as f:
    f.write(output)
