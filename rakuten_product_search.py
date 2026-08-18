#!/usr/bin/env python3
"""
Xserver上の中継スクリプト経由で、楽天商品検索APIを呼び出す。
GitHub Actionsの不定IPでは楽天APIに直接アクセスできないため、
固定IPを持つXserver経由でリクエストする。

必要な環境変数:
    RAKUTEN_PROXY_URL   (例: https://alley-oop.co.jp/tools/rakuten-proxy/rakuten-proxy.php)
    RAKUTEN_PROXY_SECRET
"""

import os
import requests


def search_product(keyword: str):
    """
    キーワードで楽天商品を検索し、最も関連性の高い商品の
    (商品名, アフィリエイトURL) を返す。見つからなければNoneを返す。
    """
    proxy_url = os.environ["RAKUTEN_PROXY_URL"]
    proxy_secret = os.environ["RAKUTEN_PROXY_SECRET"]

    params = {"secret": proxy_secret, "keyword": keyword}
    r = requests.get(proxy_url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    items = data.get("Items", [])
    if not items:
        return None

    item = items[0].get("Item", {})
    name = item.get("itemName", "")
    affiliate_url = item.get("affiliateUrl") or item.get("itemUrl")

    if not affiliate_url:
        return None

    return {"name": name, "url": affiliate_url}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("使い方: python rakuten_product_search.py <キーワード>")
        sys.exit(1)
    result = search_product(sys.argv[1])
    print(result)
