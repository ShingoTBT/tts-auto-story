import os
import requests

api_key = os.environ["ZERNIO_API_KEY_ACCOUNT3"]
account_id = os.environ["ZERNIO_THREADS_ACCOUNT_ID_2"]
post_id = "18116781415997210"

headers = {"Authorization": f"Bearer {api_key}"}
r = requests.get(
    f"https://zernio.com/api/v1/inbox/comments/{post_id}",
    headers=headers,
    params={"accountId": account_id},
    timeout=30,
)
print("status:", r.status_code)
print(r.text[:3000])
