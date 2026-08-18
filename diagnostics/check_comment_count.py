#!/usr/bin/env python3
import os
import sys
import requests

ZERNIO_API_BASE = "https://zernio.com/api/v1"

api_key = os.environ["ZERNIO_API_KEY_ACCOUNT3"]
post_id = sys.argv[1]

headers = {"Authorization": f"Bearer {api_key}"}
r = requests.get(f"{ZERNIO_API_BASE}/posts/{post_id}/comments", headers=headers, timeout=20)
print("status:", r.status_code)
print("body:", r.text[:2000])
