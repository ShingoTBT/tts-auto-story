#!/usr/bin/env python3
"""
Amazon Creators API(2026年5月にPA-API v5から移行した新API)を使って、
キーワードで商品を検索し、アフィリエイトリンク付きの商品情報を取得する。

必要な環境変数:
    AMAZON_CREATORS_CREDENTIAL_ID
    AMAZON_CREATORS_CREDENTIAL_SECRET
    AMAZON_ASSOCIATE_TAG (例: kokodawancom-22)
"""

import os
import requests

TOKEN_ENDPOINT = "https://api.amazon.com/auth/o2/token"
SEARCH_ENDPOINT = "https://creatorsapi.amazon/catalog/v1/searchItems"
MARKETPLACE = "www.amazon.co.jp"

_cached_token = None


def get_access_token() -> str:
    """LWA(Login with Amazon)方式でアクセストークンを取得する"""
    global _cached_token
    if _cached_token:
        return _cached_token

    credential_id = os.environ["AMAZON_CREATORS_CREDENTIAL_ID"]
    credential_secret = os.environ["AMAZON_CREATORS_CREDENTIAL_SECRET"]

    payload = {
        "grant_type": "client_credentials",
        "client_id": credential_id,
        "client_secret": credential_secret,
        "scope": "creatorsapi::default",
    }
    r = requests.post(TOKEN_ENDPOINT, json=payload, timeout=20)
    r.raise_for_status()
    data = r.json()
    _cached_token = data["access_token"]
    return _cached_token


def search_product(keyword: str):
    """
    キーワードでAmazon商品を検索し、最も関連性の高い商品の
    (商品名, アフィリエイトURL) を返す。見つからなければNoneを返す。
    """
    partner_tag = os.environ["AMAZON_ASSOCIATE_TAG"]
    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-marketplace": MARKETPLACE,
    }
    payload = {
        "keywords": keyword,
        "partnerTag": partner_tag,
        "marketplace": MARKETPLACE,
        "resources": [
            "images.primary.small",
            "itemInfo.title",
            "offersV2.listings.price",
        ],
    }

    r = requests.post(SEARCH_ENDPOINT, headers=headers, json=payload, timeout=20)

    # トークン切れ(401)の場合は1回だけ再取得してリトライ
    if r.status_code == 401:
        global _cached_token
        _cached_token = None
        token = get_access_token()
        headers["Authorization"] = f"Bearer {token}"
        r = requests.post(SEARCH_ENDPOINT, headers=headers, json=payload, timeout=20)

    r.raise_for_status()
    data = r.json()

    items = data.get("searchResult", {}).get("items", []) or data.get("items", [])
    if not items:
        return None

    item = items[0]
    item_info = item.get("itemInfo", {})
    title = (
        item_info.get("title", {}).get("displayValue")
        or item_info.get("title", {}).get("display_value")
        or ""
    )
    url = item.get("detailPageUrl") or item.get("detailPageURL") or item.get("DetailPageURL")

    if not url:
        return None

    return {"name": title, "url": url}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("使い方: python amazon_product_search.py <キーワード>")
        sys.exit(1)
    result = search_product(sys.argv[1])
    print(result)
