#!/usr/bin/env python3
"""
Googleトレンドの急上昇ワードRSSを取得し、
急上昇度が高い順に候補を確認、記事本文が一定文字数以上ある
最初の候補を採用してJSONで出力する。

使い方:
    python fetch_trending_news.py accounts/account2_news.yaml

出力:
    {output_dir}/trend_candidate.json
    { "keyword": "...", "article_url": "...", "article_text": "...", "source": "..." }
"""

import sys
import re
import json
from pathlib import Path

import yaml
import requests
import trafilatura
import xml.etree.ElementTree as ET

TRENDS_RSS_URL = "https://trends.google.co.jp/trending/rss?geo=JP"
MIN_ARTICLE_CHARS = 300
MAX_CANDIDATES_TO_CHECK = 10


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_traffic(approx_traffic: str) -> int:
    """'1000+' のような文字列を数値化してソート用に使う"""
    digits = re.sub(r"[^0-9]", "", approx_traffic or "0")
    return int(digits) if digits else 0


def fetch_trend_items() -> list[dict]:
    resp = requests.get(TRENDS_RSS_URL, timeout=20)
    resp.raise_for_status()

    ns = {"ht": "https://trends.google.com/trending/rss"}
    root = ET.fromstring(resp.content)
    items = []

    for item in root.findall(".//item"):
        title = item.findtext("title", default="").strip()
        traffic_el = item.find("ht:approx_traffic", ns)
        traffic = traffic_el.text if traffic_el is not None else "0"

        news_items = []
        for ni in item.findall("ht:news_item", ns):
            url_el = ni.find("ht:news_item_url", ns)
            title_el = ni.find("ht:news_item_title", ns)
            source_el = ni.find("ht:news_item_source", ns)
            if url_el is not None and url_el.text:
                news_items.append({
                    "url": url_el.text,
                    "title": title_el.text if title_el is not None else "",
                    "source": source_el.text if source_el is not None else "",
                })

        if news_items:
            items.append({
                "keyword": title,
                "traffic": parse_traffic(traffic),
                "news_items": news_items,
            })

    items.sort(key=lambda x: x["traffic"], reverse=True)
    return items


def extract_article_text(url: str) -> str:
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        text = trafilatura.extract(downloaded) or ""
        return text.strip()
    except Exception as e:
        print(f"  記事抽出エラー ({url}): {e}")
        return ""


def is_relevant(keyword: str, article_text: str) -> bool:
    """
    抽出した記事本文が、トレンドキーワードと無関係な断片でないかの簡易チェック。
    キーワードを空白等で分割し、そのいずれかが本文に含まれているかを確認する。
    """
    parts = [p for p in keyword.replace("対", " ").replace("vs", " ").split() if len(p) >= 2]
    if not parts:
        parts = [keyword]
    return any(p in article_text for p in parts)


def main():
    if len(sys.argv) < 2:
        print("使い方: python fetch_trending_news.py <account_config.yaml>")
        sys.exit(1)

    config = load_account_config(sys.argv[1])

    print("Googleトレンドを取得中...")
    trend_items = fetch_trend_items()
    print(f"{len(trend_items)}件のトレンドを取得しました")

    checked = 0
    for item in trend_items:
        if checked >= MAX_CANDIDATES_TO_CHECK:
            break
        checked += 1

        keyword = item["keyword"]
        news_item = item["news_items"][0]
        print(f"[候補{checked}] {keyword} (急上昇度:{item['traffic']}+) -> {news_item['url']}")

        article_text = extract_article_text(news_item["url"])
        char_count = len(article_text)
        print(f"  記事文字数: {char_count}")

        if char_count < MIN_ARTICLE_CHARS:
            print("  文字数不足のためスキップ")
            continue

        if not is_relevant(keyword, article_text):
            print("  抽出内容がキーワードと無関係(記事抽出の失敗)の可能性が高いためスキップ")
            continue

        result = {
            "keyword": keyword,
            "article_url": news_item["url"],
            "article_source": news_item["source"],
            "article_text": article_text[:3000],  # 長すぎる場合は先頭3000字まで
        }
        output_dir = Path(config["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "trend_candidate.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"採用: {keyword} -> {output_path}")
        return

    print("十分な文字数の候補が見つかりませんでした。今回の生成は見送ります。")

    # デバッグ用に、確認した候補一覧を記録しておく
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    debug_path = output_dir / "no_candidate_found.txt"
    with open(debug_path, "w", encoding="utf-8") as f:
        f.write(f"確認日時: {__import__('datetime').datetime.now().isoformat()}\n\n")
        for item in trend_items[:MAX_CANDIDATES_TO_CHECK]:
            f.write(f"- {item['keyword']} (急上昇度:{item['traffic']}+) -> {item['news_items'][0]['url']}\n")
    print(f"デバッグファイル: {debug_path}")

    sys.exit(1)


if __name__ == "__main__":
    main()
