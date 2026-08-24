#!/usr/bin/env python3
"""
実際にはコメント投稿せず、しきい値を超えている投稿と、
生成されるであろうコメント候補だけを確認するドライラン版。

使い方:
    python dry_run_check_comments.py accounts/account3_threads.yaml accounts/account4_threads_engagement.yaml
"""

import sys
import os
import json
import datetime
from pathlib import Path

from check_and_reply_comments import (
    load_account_config,
    load_posted_threads,
    is_within_check_window,
    get_comment_count,
    has_own_comment,
    extract_topic_keyword,
    generate_comment_phrase,
    generate_travel_fallback_phrase,
    build_travel_fallback_link,
    build_redirect_url,
    _extract_own_username,
)
from product_search import search_product


def main():
    if len(sys.argv) < 2:
        print("使い方: python dry_run_check_comments.py <account_config1.yaml> [account_config2.yaml ...]")
        sys.exit(1)

    for config_path in sys.argv[1:]:
        print(f"\n=========== {config_path} ===========")
        config = load_account_config(config_path)
        api_key = os.environ[config.get("zernio_api_key_env", "ZERNIO_API_KEY")]
        account_id = os.environ[config["zernio_account_id_env"]]
        threshold = config.get("comment_threshold", 20)
        check_days = config.get("comment_check_days", 2)
        own_username = _extract_own_username(config)

        log_path = Path(config["output_dir"]) / "posted_threads.jsonl"
        records = load_posted_threads(str(log_path))

        over_threshold_count = 0

        for record in records:
            post_id = record.get("post_id")
            if not post_id:
                continue
            if not is_within_check_window(record["posted_at"], check_days):
                continue

            try:
                count = get_comment_count(api_key, post_id, account_id)
            except Exception as e:
                print(f"  [エラー] post_id={post_id}: {e}")
                continue

            already_commented = record.get("commented", False)
            status = "対応済み" if already_commented else ("★対象" if count > threshold else "未達")
            print(f"  post_id={post_id} | コメント数={count} | {status}")

            if count > threshold and not already_commented:
                if own_username:
                    try:
                        if has_own_comment(api_key, post_id, account_id, own_username):
                            print("    → 既に自分のコメントが存在するためスキップ見込み")
                            continue
                    except Exception as e:
                        print(f"    [エラー] 既存コメント確認で失敗: {e}")
                        continue

                over_threshold_count += 1
                try:
                    text_file = record.get("text_file")
                    if not text_file or not Path(text_file).exists():
                        print("    (元テキストファイルが見つからず、コメント候補を生成できません)")
                        continue
                    post_text = Path(text_file).read_text(encoding="utf-8")

                    keyword = extract_topic_keyword(post_text, config["model"])
                    provider = os.environ.get("PRODUCT_SEARCH_PROVIDER", "amazon").lower().strip()
                    product = search_product(keyword, provider)

                    print(f"    投稿本文冒頭: {post_text[:60].strip()}...")
                    print(f"    検索キーワード: {keyword} (provider={provider})")
                    if product:
                        comment_phrase = generate_comment_phrase(post_text, product["name"], config["model"])
                        link_url = build_redirect_url(product["url"]) if provider == "rakuten" else product["url"]
                        print(f"    商品: {product['name'][:40]}")
                    else:
                        print("    関連商品が見つからず、楽天トラベルへのフォールバックを使用")
                        travel_link = build_travel_fallback_link()
                        comment_phrase = generate_travel_fallback_phrase(post_text, config["model"])
                        link_url = build_redirect_url(travel_link) if travel_link else "(RAKUTEN_AFFILIATE_ID未設定)"

                    comment_text = f"{comment_phrase}\n　↓↓\n#AD\n{link_url}"
                    print(f"    コメント候補:\n      {comment_text.replace(chr(10), chr(10)+'      ')}")
                except Exception as e:
                    print(f"    [エラー] 商品検索処理で失敗: {e}")

        print(f"\n  → {config_path}: しきい値超え・未対応の投稿は {over_threshold_count} 件")


if __name__ == "__main__":
    main()
