import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
from check_and_reply_comments import post_reply_comment

api_key = os.environ["ZERNIO_API_KEY_ACCOUNT3"]
account_id = os.environ["ZERNIO_THREADS_ACCOUNT_ID_2"]  # account4

post_id = "18116781415997210"  # https://www.threads.com/@kusetsuyo.qa/post/DdpKNeCCLXQ
test_text = (
    "これ、実はちょっと気になってるアイテムなんです\n"
    "　↓↓\n"
    "#AD\n"
    "https://item.rakuten.co.jp/"
)

result = post_reply_comment(api_key, post_id, account_id, test_text, suppress_preview=True)
print("投稿結果:", result)
