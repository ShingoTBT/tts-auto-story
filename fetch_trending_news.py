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
    # 人の不幸の話題は、題名の言葉でも確実に外す(AIの判定だけだと残ることがあった)
    items = [it for it in items if not SAD_WORDS.search(it["keyword"])]
    listing = "\n".join(f"{i}: {item['keyword']}" for i, item in enumerate(items))
    recent_listing = "\n".join(f"R{i}: {t}" for i, t in enumerate(recent_posts or []))
    rules = "".join(f"- {r}\n" for r in (topic_rules or [
        "当てはまらないもの(俳優・芸人・声優・アニメ・漫画・映画・ゲーム・スポーツ・政治・ITなど)は除外",
        "ニュースではないもの(MV・楽曲そのものの動画、歌詞、商品紹介、個人の日記)も除外",
        "歌手・バンド・アイドルなどの音楽の担い手が話の主役だとタイトルから読み取れるものだけを選ぶ。迷ったら除外",
    ]))
    prompt = (
        f"以下の「記事」一覧から「{topic}」に当てはまるニュース記事だけを選んでください。\n"
        + rules
        + "- 訃報・病気・けが・事故・事件・逮捕など、人の不幸や不祥事が中心の記事は除外\n"
        "- 失言・謝罪・炎上・批判・騒動・処分など、誰かが責められている話題も除外\n"
        "選んだ記事それぞれについて [番号, 知名度, コメント, \"主役\"] を出してください。\n"
        "- 知名度：その記事の主役が一般の人にどれだけ知られているか。5=ほぼ誰でも知っている(国民的スター・紅白常連・日本代表の顔)、"
        "4=多くの人が知っている(地上波ドラマ主演・CM・人気グループ)、3=若い人や好きな人なら知っている、2=ファンなら知っている、1=ほとんど知られていない\n"
        "- コメント：TikTok でこの話題を紹介したとき、コメントがどれだけ集まりそうか。5=賛否が分かれる・自分の意見や体験を言いたくなる・"
        "ファンが熱く語りたくなる・驚きやツッコミどころがある、3=反応はあるが盛り上がりは普通、1=淡々とした告知で言うことがない\n"
        "- 主役：その記事の中心の人物・グループの名前を、正式な名前で短く(例:\"松井秀喜\"、\"Mrs. GREEN APPLE\"、\"YOASOBI\")。"
        "同じ人物・グループなら、どの記事でも必ず同じ書き方にする\n"
        + ("さらに、下の「直近の投稿」それぞれについても [\"R番号\", 0, 0, \"主役\"] を同じ書き方で出してください(同じ話題を続けないために使います)。\n"
           if recent_listing else "")
        + "回答は JSON の配列1つだけにすること(例: [[3, 5, 4, \"松井秀喜\"], [0, 2, 3, \"INI\"], [\"R0\", 0, 0, \"YOASOBI\"]])。説明・理由・見出しは一切書かない。\n\n"
        "【記事】\n" + listing
        + ("\n\n【直近の投稿】\n" + recent_listing if recent_listing else "")
    )
    client = anthropic.Anthropic()
    rows = None
    for attempt in range(2):   # 説明だけ書いて答えを出さないことがあるので、1回だけやり直す
        response = client.messages.create(
            model=model,
            **claude_extra_params(model),
            max_tokens=6000,
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

    # 並べ替え・同じ主役の除外はプログラムで確実に行う(10-23 AIに任せると同じ出来事が続いた)
    def norm(name):
        return re.sub(r"[\s・･\.\-_]|氏$|さん$|選手$|監督$", "", str(name)).lower()

    used = set()
    scored = []
    for row in rows:
        try:
            key, star = row[0], norm(row[-1])
            fame = int(row[1])
            comment = int(row[2]) if len(row) >= 4 else 3
        except Exception:
            continue
        if isinstance(key, str) and key.startswith("R"):
            used.add(star)
            continue
        try:
            idx = int(key)
        except Exception:
            continue
        if 0 <= idx < len(items):
            scored.append((fame, comment, idx, star))
    # 並べ方(ユーザー希望 10-23): 知名度の高い人(4以上)を先に → その中でコメントが集まりそうな順 → 知名度 → 同点はランダム
    scored.sort(key=lambda x: (-(x[0] >= 4), -x[1], -x[0], random.random()))
    print(f"  直近の投稿の主役(続けない): {sorted(used)}")
    picked = []
    for fame, comment, idx, star in scored:
        if star and star in used:
            continue   # 直近に投稿した主役、またはすでに選んだ記事と同じ主役
        picked.append(items[idx])
        if len(picked) <= 10:
            print(f"    知名度{fame}・コメント{comment}：{items[idx]['keyword'][:50]}")
        if star:
            used.add(star)
    return picked


# 人の不幸・不祥事の話題(題名にこれらの言葉があれば選ばない)
SAD_WORDS = re.compile(r"死去|訃報|逝去|追悼|悼む|亡くな|永眠|命日|葬儀|お別れ会|病気|闘病|がん|癌|入院|手術|重体|事故|逮捕|容疑|書類送検|不祥事|被害"
                       # 10-23 ユーザー「失言、謝罪はやめましょう」
                       r"|謝罪|お詫び|おわび|陳謝|炎上|失言|釈明|批判|騒動|不倫|処分|物議")


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
        text = (trafilatura.extract(downloaded) or "").strip()
        # 10-28 Yahoo!ニュースのトピックス(pickup)は要約だけなので、元の記事までたどって読む
        # (要約だけで解説を書かせたら、記事に無い理由を補ってしまったため)
        if "news.yahoo.co.jp/pickup/" in url:
            m = re.search(r"https://news\.yahoo\.co\.jp/articles/[0-9a-f]+", downloaded)
            if m:
                full = (trafilatura.extract(trafilatura.fetch_url(m.group(0)) or "") or "").strip()
                if full:
                    print(f"  要約ページから元の記事へ: {m.group(0)}(記事 {len(full)}字＋要約 {len(text)}字)")
                    return full + "\n\n【Yahoo!ニュースの要約ページ】\n" + text
        return text
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

    # 10-25〜 ユーザーが「次の1回だけこのネタで」と指定したもの(<output_dir>/next_topic.json)。使ったら消える
    next_topic = Path(config["output_dir"]) / "next_topic.json"
    if next_topic.exists():
        from buzz_followup import angle_note
        want = json.loads(next_topic.read_text(encoding="utf-8"))
        # 前に同じネタで作った投稿が複数あるときは、全部を「前回の投稿」として渡す(どれとも違う切り口にするため)
        sources = [want["source"]] + want.get("also_sources", [])
        post_text = "\n\n----------\n\n".join(Path(s).read_text(encoding="utf-8") for s in sources if Path(s).exists())
        result = {"keyword": want["keyword"], "article_url": want.get("article_url", ""), "article_source": "",
                  "article_text": want.get("article_text") or post_text, "buzz_root": want["source"],
                  "angle_note": angle_note({"title": want["title"], "post_text": post_text})}
        with open(Path(config["output_dir"]) / "trend_candidate.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        next_topic.unlink()
        print(f"採用(ユーザー指定の1回だけのネタ): {want['title']}")
        return

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
        # 10-28〜 題名にこの言葉がある記事は、AI に見せる前に外す(解説ニュースで政治の話を確実に避けるため。設定 topic_exclude_regex)
        if config.get("topic_exclude_regex"):
            ng = re.compile(config["topic_exclude_regex"])
            dropped = [i["keyword"] for i in all_items if ng.search(i["keyword"])]
            all_items = [i for i in all_items if not ng.search(i["keyword"])]
            if dropped:
                print(f"  決めた言葉で外した記事: {len(dropped)}件(例: {dropped[:3]})")
        print(f"{len(all_items)}件の記事を取得しました。ジャンルに合うものを選びます...")
        # 10-25〜 2日前までの投稿にバズったものがあれば、同じネタを別の切り口で(buzz_followup.py)
        if config.get("buzz_followup"):
            try:
                from buzz_followup import find_buzz, pick_buzz_candidate
                buzz = find_buzz(config)
                if buzz:
                    result = pick_buzz_candidate(all_items, buzz, extract_article_text, MIN_ARTICLE_CHARS)
                    output_dir = Path(config["output_dir"])
                    output_dir.mkdir(parents=True, exist_ok=True)
                    with open(output_dir / "trend_candidate.json", "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    print(f"採用(バズったネタ): {result['keyword']}")
                    return
            except Exception as e:
                print(f"  バズの確認ができませんでした(いつもどおり選びます): {e}")
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
