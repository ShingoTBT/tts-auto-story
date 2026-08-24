import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    result = search_product("プチギフト かわいい雑貨")
    print("検索結果:", json.dumps(result, ensure_ascii=False, indent=2))

    if result:
        from check_and_reply_comments import generate_comment_phrase
        post = """友人グループで毎月「積み立て」をして、誰かの誕生日に豪華なプレゼントを贈る文化があります。
でも今月、私の誕生日に渡されたのは明らかに積立額より安い物で
今月ちょっと厳しくてって言われたけど、私の番だけ減らされたことにモヤモヤが止まらなくて。"""
        phrase = generate_comment_phrase(post, result["name"], "claude-sonnet-4-6")
        comment_text = f"{phrase}\n\u3000\u2193\u2193\n#AD\n{result['url']}"
        print("\n=== 完成イメージ ===")
        print(comment_text)
except Exception as e:
    print("検索エラー:", repr(e))
    if hasattr(e, "response") and e.response is not None:
        print("body:", e.response.text[:1000])

with open("diagnostics/amazon_check_result.txt", "w", encoding="utf-8") as f:
    f.write("done - see logs above via stdout capture")
