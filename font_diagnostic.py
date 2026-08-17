#!/usr/bin/env python3
"""
フォント修正が正しく効いているかを確認するための診断スクリプト。
「写」など、中国語字形と日本語字形で差が出やすい漢字を含む固定テキストを
実際のindex.phpに投入し、画像化して保存する。
"""

from pathlib import Path
from render_images import render

TEST_TEXT = """■フォント診断テスト

写真を撮りに行きました。彼女は自然な写真が好きです。
辞書で「言」という字と「今」という字を確認しました。
直接、来週、写真展に行く予定です。

=====

道具箱の中に、古い写真が入っていました。
新聞紙に包まれた写真立てを見つけました。

=====

結局、写真は見つかりませんでした。
様々な場所を探しましたが、無駄でした。

#フォント診断 #テスト
"""

IMAGE_TOOL_URL = "https://alley-oop.co.jp/tools/TikTok-Story/"
OUTPUT_DIR = Path("outputs/font_diagnostic")

image_paths, copy_text = render(IMAGE_TOOL_URL, TEST_TEXT, OUTPUT_DIR, "font_diagnostic")

print("生成された画像:")
for p in image_paths:
    print(" -", p)
