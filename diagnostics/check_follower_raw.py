#!/usr/bin/env python3
import os
import datetime
import requests

ZERNIO_API_BASE = "https://zernio.com/api/v1"

api_key = os.environ["ZERNIO_API_KEY"]
account_id = os.environ["ZERNIO_TIKTOK_ACCOUNT_ID"]

headers = {"Authorization": f"Bearer {api_key}"}
today = datetime.date.today().isoformat()
week_ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

params = {
    "accountIds": account_id,
    "fromDate": week_ago,
    "toDate": today,
    "granularity": "daily",
}
r = requests.get(f"{ZERNIO_API_BASE}/accounts/follower-stats", headers=headers, params=params, timeout=20)
result = f"status: {r.status_code}\nbody: {r.text}"
print(result)

with open("diagnostics/follower_check_result.txt", "w", encoding="utf-8") as f:
    f.write(result)
