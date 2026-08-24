#!/usr/bin/env python3
"""
商品検索の切り替え窓口。
PRODUCT_SEARCH_PROVIDER環境変数(またはaccount設定のproduct_search_provider)で
"amazon" か "rakuten" を指定して切り替える。デフォルトは "amazon"。

戻り値の形式はどちらのプロバイダでも共通:
    {"name": "商品名", "url": "アフィリエイトURL"} または None
"""

import os


def search_product(keyword: str, provider: str = None):
    provider = provider or os.environ.get("PRODUCT_SEARCH_PROVIDER", "amazon")
    provider = provider.lower().strip()

    if provider == "amazon":
        from amazon_product_search import search_product as _search
        return _search(keyword)
    elif provider == "rakuten":
        from rakuten_product_search import search_product as _search
        return _search(keyword)
    else:
        raise ValueError(f"不明なprovider: {provider} (amazon または rakuten を指定してください)")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("使い方: python product_search.py <キーワード> [amazon|rakuten]")
        sys.exit(1)
    provider = sys.argv[2] if len(sys.argv) > 2 else None
    result = search_product(sys.argv[1], provider)
    print(result)
