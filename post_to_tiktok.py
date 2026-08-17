#!/usr/bin/env python3
"""
render_images.pyが生成した画像(GitHub上の公開URL)をZernio API経由で
TikTokに直接投稿する(Direct Post、完全自動・人の操作なし)。

使い方:
    python post_to_tiktok.py accounts/account1_emotional.yaml <source_text_file.txt>

必要な環境変数:
    ZERNIO_API_KEY
    ZERNIO_TIKTOK_ACCOUNT_ID
"""

import sys
import os
import time
from pathlib import Path

import yaml
import requests

ZERNIO_API_BASE = "https://zernio.com/api/v1"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/ShingoTBT/tts-auto-story/main"


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_public_image_urls(config: dict, file_prefix: str, count: int) -> list[str]:
    urls = []
    for i in range(1, count + 1):
        rel_path = f"{config['output_dir']}/images/{file_prefix}_{i:02d}.png"
        urls.append(f"{GITHUB_RAW_BASE}/{rel_path}")
    return urls


def extract_caption(source_text: str) -> str:
    """先頭のタイトル行(■...)とハッシュタグ行を組み合わせてキャプションにする"""
    lines = [l for l in source_text.split("\n") if l.strip() != ""]
    title = lines[0].strip() if lines else ""
    hashtag_lines = [l for l in lines if l.strip().startswith("#")]
    hashtags = hashtag_lines[0] if hashtag_lines else ""
    return f"{title}\n\n{hashtags}".strip()


def create_post(api_key: str, account_id: str, image_urls: list[str], caption: str) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "content": caption,
        "mediaItems": [{"type": "image", "url": url} for url in image_urls],
        "platforms": [
            {"platform": "tiktok", "accountId": account_id}
        ],
        "tiktokSettings": {
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "media_type": "photo",
            "photo_cover_index": 0,
            "description": caption,
        },
        "publishNow": True,
    }

    r = requests.post(f"{ZERNIO_API_BASE}/posts", headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    if len(sys.argv) < 3:
        print("使い方: python post_to_tiktok.py <account_config.yaml> <source_text_file.txt>")
        sys.exit(1)

    config_path = sys.argv[1]
    source_text_path = Path(sys.argv[2])

    config = load_account_config(config_path)
    api_key = os.environ["ZERNIO_API_KEY"]
    account_id = os.environ["ZERNIO_TIKTOK_ACCOUNT_ID"]

    source_text = source_text_path.read_text(encoding="utf-8")
    caption = extract_caption(source_text)

    file_prefix = source_text_path.stem
    image_urls = build_public_image_urls(config, file_prefix, count=3)

    print("投稿する画像URL:")
    for u in image_urls:
        print(" -", u)

    result = create_post(api_key, account_id, image_urls, caption)
    print("Zernio投稿結果:", result)


if __name__ == "__main__":
    main()
