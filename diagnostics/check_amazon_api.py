import os
import json
from amazon_product_search import search_product, get_access_token

try:
    token = get_access_token()
    print("トークン取得成功:", token[:20] + "...")
except Exception as e:
    print("トークン取得エラー:", repr(e))
    if hasattr(e, "response") and e.response is not None:
        print("body:", e.response.text[:1000])
    raise SystemExit(1)

try:
    result = search_product("コーヒー")
    print("検索結果:", json.dumps(result, ensure_ascii=False, indent=2))
except Exception as e:
    print("検索エラー:", repr(e))
    if hasattr(e, "response") and e.response is not None:
        print("body:", e.response.text[:1000])

with open("diagnostics/amazon_check_result.txt", "w", encoding="utf-8") as f:
    f.write("done - see logs above via stdout capture")
