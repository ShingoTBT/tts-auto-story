import os
import requests

api_key = os.environ["ZERNIO_API_KEY_ACCOUNT3"]
account_id = os.environ["ZERNIO_THREADS_ACCOUNT_ID_2"]
post_id = "18116781415997210"
comment_id = "17910415938542123"

headers = {"Authorization": f"Bearer {api_key}"}
r = requests.delete(
    f"https://zernio.com/api/v1/inbox/comments/{post_id}/{comment_id}",
    headers=headers,
    params={"accountId": account_id},
    timeout=20,
)
print("status:", r.status_code)
print(r.text[:1000])
