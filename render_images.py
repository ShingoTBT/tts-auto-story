#!/usr/bin/env python3
"""
generate.pyが出力したテキストファイルを、既存のTikTok感動ストーリー画像生成ツール
(index.php)に自動投入し、生成された画像(PNG)とコピー用テキスト(タイトル+ハッシュタグ)を
保存する。

使い方:
    python render_images.py accounts/account1_emotional.yaml <生成テキストファイルのパス>
"""

import sys
import base64
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def render(image_tool_url: str, source_text: str, output_dir: Path, file_prefix: str):
    output_dir.mkdir(parents=True, exist_ok=True)
    image_paths = []
    copy_text = ""

    last_error = None
    for attempt in range(1, 3):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(image_tool_url, wait_until="networkidle", timeout=30000)

                # 本文を入力
                page.fill("#sourceText", source_text)

                # 画像を生成するボタンをクリック
                page.click("#generateBtn")

                # 結果カードが表示されるまで待つ(サーバー側が遅い場合に備えて余裕を持たせる)
                page.wait_for_selector("#resultsCard", state="visible", timeout=30000)

                image_paths, copy_text = _extract_results(page, output_dir, file_prefix)
                browser.close()
                return image_paths, copy_text
        except Exception as e:
            last_error = e
            print(f"[試行{attempt}] 画像化に失敗しました: {e}")
            try:
                debug_dir = output_dir / "debug"
                debug_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(debug_dir / f"{file_prefix}_error_attempt{attempt}.png"))
                with open(debug_dir / f"{file_prefix}_error_attempt{attempt}.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception as e2:
                print(f"デバッグ情報の保存にも失敗しました: {e2}")
            try:
                browser.close()
            except Exception:
                pass

    raise last_error


def _extract_results(page, output_dir: Path, file_prefix: str):
    image_paths = []

    # 生成された画像(dataURL)をすべて取得
    img_data_urls = page.eval_on_selector_all(
        "#imageList .preview-wrap img",
        "elements => elements.map(el => el.src)"
    )

    for i, data_url in enumerate(img_data_urls, start=1):
        # "data:image/png;base64,xxxxx" からbase64部分を取り出す
        header, encoded = data_url.split(",", 1)
        image_bytes = base64.b64decode(encoded)

        image_path = output_dir / f"{file_prefix}_{i:02d}.png"
        with open(image_path, "wb") as f:
            f.write(image_bytes)
        image_paths.append(str(image_path))

    # コピー用テキスト(タイトル+ハッシュタグ)も取得
    copy_text = page.inner_text("#copyTextBox")

    return image_paths, copy_text


def main():
    if len(sys.argv) < 3:
        print("使い方: python render_images.py <account_config.yaml> <source_text_file.txt>")
        sys.exit(1)

    config_path = sys.argv[1]
    source_text_path = sys.argv[2]

    config = load_account_config(config_path)

    with open(source_text_path, "r", encoding="utf-8") as f:
        source_text = f.read()

    output_dir = Path(config["output_dir"]) / "images"
    file_prefix = Path(source_text_path).stem

    image_paths, copy_text = render(
        config["image_tool_url"], source_text, output_dir, file_prefix
    )

    print(f"生成された画像: {len(image_paths)}枚")
    for p in image_paths:
        print(" -", p)

    copy_text_path = output_dir / f"{file_prefix}_caption.txt"
    with open(copy_text_path, "w", encoding="utf-8") as f:
        f.write(copy_text)
    print(f"キャプション: {copy_text_path}")


if __name__ == "__main__":
    main()
