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
import random
from pathlib import Path
from urllib.parse import urlparse

import yaml
import requests
import trafilatura
import xml.etree.ElementTree as ET

TRENDS_RSS_URL = "https://trends.google.co.jp/trending/rss?geo=JP"
HATENA_RSS_URLS = [
    "https://b.hatena.ne.jp/hotentry/entertainment.rss",
    "https://b.hatena.ne.jp/hotentry/social.rss",
]
MIN_ARTICLE_CHARS = 300
MAX_CANDIDATES_TO_CHECK = 10

# topic_filter(アカウント設定)があるときだけ使う、ジャンル専門のニュースRSS
ATOM_NS = "http://www.w3.org/2005/Atom"
RSS1_NS = "http://purl.org/rss/1.0/"
TOPIC_FILTER_DEFAULT_MODEL = "claude-sonnet-4-6"
TOPIC_FILTER_MAX_TITLES = 240  # 10-23 芸能・スポーツの取得先を増やしたので広げた(80だと後ろの取得先が切れる)
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
# 動画・SNSのページはニュース記事ではないので、ジャンル指定のときは候補に入れない
NON_ARTICLE_DOMAINS = ("youtube.com", "youtu.be", "nicovideo.jp", "x.com", "twitter.com", "tiktok.com", "instagram.com")
# AIでの絞り込みに失敗したときの予備(タイトルにこの語があれば対象とみなす)
TOPIC_FALLBACK_KEYWORDS_DEFAULT = [
    "アイドル", "歌手", "バンド", "ミュージシャン", "アーティスト", "シンガー", "ラッパー",
    "新曲", "アルバム", "シングル", "ライブ", "ツアー", "コンサート", "ドーム", "紅白",
    "MV", "メンバー", "グループ", "K-POP", "坂46", "AKB48", "ジャニーズ", "STARTO", "音楽",
]


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_traffic(approx_traffic: str) -> int:
    """'1000+' のような文字列を数値化してソート用に使う"""
    digits = re.sub(r"[^0-9]", "", approx_traffic or "0")
    return int(digits) if digits else 0


def fetch_trend_items() -> list[dict]:
    """Googleトレンド(急上昇ワード)を取得する"""
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


def fetch_hatena_items() -> list[dict]:
    """
    はてなブックマークの人気エントリー(エンタメ・世の中カテゴリ)を取得する。
    Googleトレンドと同じ形式({keyword, traffic, news_items})に変換して返す。

    はてなのフィードはRSS1.0(RDF)形式で、要素がデフォルト名前空間
    (http://purl.org/rss/1.0/)に属しているため、名前空間を明示して検索する必要がある。
    """
    RSS1_NS = "http://purl.org/rss/1.0/"
    ns = {"rss": RSS1_NS}
    items = []

    for rss_url in HATENA_RSS_URLS:
        try:
            resp = requests.get(rss_url, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"  はてなRSS取得エラー ({rss_url}): {e}")
            continue

        try:
            root = ET.fromstring(resp.content)
        except Exception as e:
            print(f"  はてなRSS解析エラー ({rss_url}): {e}")
            continue

        for item in root.findall(".//rss:item", ns):
            title = item.findtext("rss:title", default="", namespaces=ns).strip()
            link = item.findtext("rss:link", default="", namespaces=ns).strip()
            if not title or not link:
                continue

            # タイトル末尾の "(123 users)" 等からブックマーク数を抽出できる場合がある
            m = re.search(r"\((\d+)\s*users?\)", title)
            traffic = int(m.group(1)) if m else 0

            items.append({
                "keyword": title,
                "traffic": traffic,
                "news_items": [{"url": link, "title": title, "source": "はてなブックマーク"}],
            })

    items.sort(key=lambda x: x["traffic"], reverse=True)
    return items


def fetch_feed_items(rss_url: str) -> list[dict]:
    """RSS1.0 / RSS2.0 / Atom のどれでも読み、{keyword, traffic, news_items} の形で返す"""
    try:
        resp = requests.get(rss_url, timeout=20, headers={"User-Agent": BROWSER_UA})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"  RSS取得エラー ({rss_url}): {e}")
        return []

    entries = []
    for item in root.findall(f".//{{{RSS1_NS}}}item"):
        entries.append((item.findtext(f"{{{RSS1_NS}}}title", default=""),
                        item.findtext(f"{{{RSS1_NS}}}link", default="")))
    for item in root.findall(".//channel/item"):
        entries.append((item.findtext("title", default=""), item.findtext("link", default="")))
    for entry in root.findall(f".//{{{ATOM_NS}}}entry"):
        link_el = entry.find(f"{{{ATOM_NS}}}link")
        entries.append((entry.findtext(f"{{{ATOM_NS}}}title", default=""),
                        link_el.get("href", "") if link_el is not None else ""))

    source = urlparse(rss_url).netloc
    items = []
    for title, link in entries:
        title, link = title.strip(), link.strip()
        if title and link:
            items.append({
                "keyword": title,
                "traffic": 0,
                "news_items": [{"url": link, "title": title, "source": source}],
            })
    return items


def fetch_topic_items(config: dict) -> list[dict]:
    """はてなの人気エントリー＋ジャンル専門RSSを集める(はてなを先に=話題性の高い順)"""
    items = fetch_hatena_items()
    for rss_url in config.get("topic_extra_rss", []):
        items.extend(fetch_feed_items(rss_url))

    seen_urls = set()
    unique = []
    for item in items:
        url = item["news_items"][0]["url"]
        if url in seen_urls:
            continue
        host = urlparse(url).netloc.lower()
        if any(host == d or host.endswith("." + d) for d in NON_ARTICLE_DOMAINS):
            continue
        seen_urls.add(url)
        unique.append(item)
    return unique


def select_topic_items_with_ai(items: list[dict], topic: str, model: str, recent_posts: list[str] = (), topic_rules=None) -> list[dict]:
    """タイトル一覧から topic に当てはまるニュースだけを選ばせ、知名度の高い順に並べる。
    人の不幸の話題・直近に投稿した話題と同じ人物や出来事は除く(2026-10-23 YOASOBI が3本続いたため)"""
    import anthropic
    from generate import claude_extra_params

    items = items[:TOPIC_FILTER_MAX_TITLES]
    listing = "\n".join(f"{i}: {item['keyword']}" for i, item in enumerate(items))
    rules = "".join(f"- {r}\n" for r in (topic_rules or [
        "当てはまらないもの(俳優・芸人・声優・アニメ・漫画・映画・ゲーム・スポーツ・政治・ITなど)は除外",
        "ニュースではないもの(MV・楽曲そのものの動画、歌詞、商品紹介、個人の日記)も除外",
        "歌手・バンド・アイドルなどの音楽の担い手が話の主役だとタイトルから読み取れるものだけを選ぶ。迷ったら除外",
    ]))
    prompt = (
        f"以下はニュース・記事のタイトル一覧です。「{topic}」に当てはまるニュース記事だけを選んでください。\n"
        + rules
        + "選んだ記事それぞれについて [番号, 知名度, 除外] を出してください。\n"
        "- 知名度：その記事の主役が、一般の人にどれだけ知られているか。5=ほぼ誰でも知っている(紅白常連・国民的スター・日本代表の顔)、"
        "4=多くの人が知っている(地上波ドラマ主演・CM・人気グループ)、3=若い人や好きな人なら知っている、2=ファンなら知っている、1=ほとんど知られていない\n"
        "- 除外：次のどれかなら 1、それ以外は 0。(a)訃報・病気・けが・事故・事件の被害など人の不幸が中心の話題 "
        + ("(b)次の直近2日に投稿した話題と同じ人物・グループ、または同じ出来事(記事が違っても)\n"
           + "\n".join(f"  ・{t}" for t in recent_posts) + "\n" if recent_posts else "\n")
        + "回答は JSON の配列1つだけにすること(例: [[3, 5, 0], [0, 2, 1]])。説明・理由・見出しは一切書かない。当てはまる記事が無ければ []。\n\n"
        f"{listing}"
    )
    client = anthropic.Anthropic()
    rows = None
    for attempt in range(2):   # 説明だけ書いて答えを出さないことがあるので、1回だけやり直す
        response = client.messages.create(
            model=model,
            **claude_extra_params(model),
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            rows = json.loads(text[text.index("["):text.rindex("]") + 1])
            break
        except Exception:
            print(f"  AIの回答を読めなかったのでやり直します: {text[:80]}")
    if rows is None:
        raise ValueError(f"AIの回答を読めませんでした: {text[:200]}")

    # 並べ替えと除外はプログラムで確実に行う(10-23 AIに任せると訃報が残る・知名度順にならない・同じ出来事を続けて選んだ)
    scored = []
    for row in rows:
        try:
            idx, fame, ng = int(row[0]), int(row[1]), int(row[2])
        except Exception:
            continue
        if 0 <= idx < len(items) and not ng:
            scored.append((fame, idx))
    scored.sort(key=lambda x: -x[0])   # 知名度の高い順(同じなら AI の並び順)
    picked, used_names = [], recent_names(recent_posts)
    for fame, idx in scored:
        item = items[idx]
        names = title_names(item["keyword"])
        if names & used_names:
            continue   # 直近の投稿や、すでに選んだ記事と同じ人物・出来事
        picked.append(item)
        used_names |= names
    return picked


# 同じ人物・出来事かどうかを、題名の中の固有名らしい言葉(漢字・カタカナ・英字が4文字以上続くもの)で見分ける
_COMMON_WORDS = {
    "ニュース", "アルバム", "シングル", "ツアー", "ライブ", "コンサート", "スポーツ", "日刊スポーツ", "スポーツ報知",
    "オリコン", "ドラマ", "インタビュー", "オフィシャルレポート", "リリース", "メンバー", "グループ", "デビュー",
    "YouTube", "TikTok", "Instagram", "ABEMA", "TIMES", "PRIME", "週刊女性PRIME", "Yahoo", "NHKニュース",
    "公開決定", "開催決定", "発売決定", "初登場", "新曲配信", "日本代表", "代表選手", "記者会見",
    "レジェンド", "電撃就任", "ギャップ", "サプライズ", "ファン歓喜",
}


def title_names(title: str) -> set:
    t = re.split(r" - | \| |（|\(", title)[0]
    words = set(re.findall(r"[一-龯々ァ-ヴー・A-Za-z0-9＆&]{4,}", t))
    return {w for w in words if w not in _COMMON_WORDS and not w.isdigit()}


def recent_names(recent_posts) -> set:
    names = set()
    for t in recent_posts or []:
        # 「題名 (元の記事)」の両方から拾う
        for part in t.split(" (", 1):
            names |= title_names(part.rstrip(")"))
    return names


def select_topic_items_by_keywords(items: list[dict], keywords: list[str]) -> list[dict]:
    """AIが使えないときの予備。はてな(何の話題もある)の記事は、言葉が合っても音楽以外が混ざるので使わず、
    音楽ニュースのサイト(topic_extra_rss)の記事だけを使う(10-23「5人グループで座って」などを拾ったため)"""
    return [item for item in items if item["news_items"][0].get("source") != "はてなブックマーク"
            and any(k in item["keyword"] for k in keywords)] or \
           [item for item in items if item["news_items"][0].get("source") != "はてなブックマーク"]


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


def load_recent_keywords(titles_log_file: str, hours: int = 24) -> set[str]:
    """直近使用済みのトレンドキーワードを、期間指定で読み込む(重複投稿の機械的ブロック用)"""
    path = Path(titles_log_file)
    if not path.exists():
        return set()

    import datetime
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
    keywords = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                generated_at = datetime.datetime.fromisoformat(record["generated_at"])
                if generated_at >= cutoff:
                    # "■題名 (キーワード)" 形式にも対応(キーワードの中の「(」で切れないよう、最初の " (" で分ける)
                    title = record.get("title", "")
                    keyword_part = title.split(" (", 1)[1][:-1] if " (" in title and title.endswith(")") else title
                    keywords.add(keyword_part)
            except Exception:
                continue
    return keywords


def load_recent_post_titles(titles_log_file: str, hours: int = 48) -> list[str]:
    """直近に投稿した題名(「■題名 (元の記事)」のまま)。ネタ選びの AI に渡して同じ話題を避ける"""
    import datetime
    path = Path(titles_log_file)
    if not path.exists():
        return []
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
    titles = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            if datetime.datetime.fromisoformat(record["generated_at"]) >= cutoff:
                titles.append(record.get("title", "").lstrip("■").strip())
        except Exception:
            continue
    return titles[-15:]


def main():
    if len(sys.argv) < 2:
        print("使い方: python fetch_trending_news.py <account_config.yaml>")
        sys.exit(1)

    config = load_account_config(sys.argv[1])

    # ジャンル指定のときは、一度使った記事を2週間使わない(10-23 同じ記事が2日後にまた選ばれたため)。それ以外は今までどおり24時間
    reuse_hours = 24 * int(config.get("topic_reuse_days", 14)) if config.get("topic_filter") else 24
    recent_keywords = load_recent_keywords(config["titles_log_file"], hours=reuse_hours)
    if recent_keywords:
        print(f"直近{reuse_hours}時間で使用済みのキーワード({len(recent_keywords)}件)")

    # google_trend_ratio: 0.0〜1.0。Googleトレンドを使う確率。
    # 0.0 なら常にはてな、1.0 なら常にGoogle、0.5なら半々でランダムに混在。
    google_ratio = float(config.get("google_trend_ratio", 0.0))
    use_google = random.random() < google_ratio
    topic = config.get("topic_filter")

    if topic:
        # ジャンル指定あり: はてな＋専門RSSから、ジャンルに合うものだけを話題性の順に使う
        print(f"ジャンル指定: {topic}")
        all_items = [i for i in fetch_topic_items(config) if i["keyword"] not in recent_keywords]
        print(f"{len(all_items)}件の記事を取得しました。ジャンルに合うものを選びます...")
        try:
            recent_posts = load_recent_post_titles(config["titles_log_file"], hours=48)
            print(f"直近2日の投稿({len(recent_posts)}件)と同じ話題は避けます")
            trend_items = select_topic_items_with_ai(
                all_items, topic, config.get("topic_filter_model", TOPIC_FILTER_DEFAULT_MODEL), recent_posts,
                config.get("topic_filter_rules"),
            )
            print(f"AIが選んだ記事: {len(trend_items)}件")
        except Exception as e:
            print(f"  AIでの絞り込みに失敗したため、キーワードで絞り込みます: {e}")
            trend_items = select_topic_items_by_keywords(
                all_items, config.get("topic_fallback_keywords", TOPIC_FALLBACK_KEYWORDS_DEFAULT)
            )
        for item in trend_items[:MAX_CANDIDATES_TO_CHECK]:
            print(f"  - {item['keyword']}")
        source_type = "topic"
    elif use_google:
        print(f"今回はGoogleトレンドを使用します(google_trend_ratio={google_ratio})")
        print("Googleトレンドを取得中...")
        trend_items = fetch_trend_items()
        source_type = "google"
    else:
        print(f"今回ははてなブックマークを使用します(google_trend_ratio={google_ratio})")
        print("はてなブックマークの人気エントリーを取得中...")
        trend_items = fetch_hatena_items()
        source_type = "hatena"

    print(f"{len(trend_items)}件の候補を取得しました")

    checked = 0
    for item in trend_items:
        if checked >= MAX_CANDIDATES_TO_CHECK:
            break

        keyword = item["keyword"]

        if keyword in recent_keywords:
            print(f"[スキップ] {keyword} は直近24時間以内に使用済みです")
            continue

        checked += 1
        news_item = item["news_items"][0]
        print(f"[候補{checked}] {keyword} (急上昇度:{item['traffic']}+) -> {news_item['url']}")

        article_text = extract_article_text(news_item["url"])
        char_count = len(article_text)
        print(f"  記事文字数: {char_count}")

        if char_count < MIN_ARTICLE_CHARS:
            print("  文字数不足のためスキップ")
            continue

        # はてなブックマークは「キーワード=記事タイトルそのもの(同一記事)」なので、
        # Googleトレンド用に作られた関連性チェックは不要(むしろ誤判定の原因になる)
        if source_type == "google" and not is_relevant(keyword, article_text):
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
