"""
ニュース(アカウント2): 2日前までの投稿の中にバズったものがあれば、次の1本は同じネタを別の切り口で作る(2026-10-25〜 ユーザー希望)

- バズ = 投稿から3時間以上たっていて、再生数が「ここ2週間のニュース動画の再生数の中央値の2倍以上」かつ「500回以上」
- 同じネタの新しい記事があればそれを使い、無ければバズった投稿と同じ記事(または投稿の本文)を、切り口を変えて使う
- 同じネタで続けるのは5回まで。続けて出さず、必ず間に別のネタの投稿を1本以上はさむ(10-31〜 ユーザー希望。前は2回まで・すぐ次の回)
- 前回の追いかけの投稿が伸びなかった(再生数がふだんの中央値より下)ら、そのネタはそこでやめる

fetch_trending_news.py から find_buzz → pick_buzz_candidate を呼び、
generate_news.py は作った投稿の元ネタを log_candidate で news_candidates.jsonl に残す(次にバズったときに元の記事を使うため)
"""

import datetime
import json
import os
import statistics
from pathlib import Path

import requests

ZERNIO_API_BASE = "https://zernio.com/api/v1"
BUZZ_MIN_VIEWS = 500
BUZZ_RATIO = 2.0
MIN_AGE_HOURS = 3
LOOK_BACK_HOURS = 96   # 10-26 直近3本 → 2日前まで(ユーザー希望。3本だと半日で外れて、バズを拾えなかった)。10-31 間をあけて何回も使うので4日前までに
MAX_FOLLOWUPS = 5      # 10-31 2 → 5(ユーザー「1回だけだともったいない」)
MEDIAN_DAYS = 14
CANDIDATE_LOG = "news_candidates.jsonl"
PICK_MODEL = "claude-sonnet-4-6"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(raw))
        except Exception:
            continue
    return rows


def log_candidate(output_dir: str, source_path, candidate: dict) -> None:
    """投稿の元ネタ(記事)を残す。source は posted_videos.jsonl の source と同じ書き方"""
    row = {"source": str(source_path).replace("\\", "/"), "saved_at": datetime.datetime.now().isoformat()}
    for k in ("keyword", "article_url", "article_source", "article_text", "buzz_root"):
        if candidate.get(k):
            row[k] = candidate[k]
    with open(Path(output_dir) / CANDIDATE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _tiktok_posts(config: dict) -> list[dict]:
    """ここ2週間の投稿と再生数(Zernio)。[{published, content, views}]"""
    key = os.environ.get(config.get("zernio_api_key_env", "ZERNIO_API_KEY"), "").strip()
    account_id = os.environ.get(config.get("zernio_account_id_env", ""), "").strip()
    if not key or not account_id:
        raise RuntimeError("Zernio の鍵かアカウントIDがありません")
    today = datetime.date.today()
    r = requests.get(f"{ZERNIO_API_BASE}/analytics", headers={"Authorization": f"Bearer {key}"}, timeout=30, params={
        "platform": "tiktok", "accountId": account_id, "limit": 50,
        "fromDate": (today - datetime.timedelta(days=MEDIAN_DAYS)).isoformat(),
        "toDate": (today + datetime.timedelta(days=1)).isoformat(),
    })
    r.raise_for_status()
    posts = []
    for p in r.json().get("posts", []):
        try:
            published = datetime.datetime.fromisoformat((p.get("publishedAt") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        posts.append({"published": published, "content": (p.get("content") or "").strip(),
                      "views": (p.get("analytics") or {}).get("views") or 0})
    return posts


def find_buzz(config: dict):
    """4日前までの投稿の中でバズったものを探す。無ければ None"""
    out = Path(config["output_dir"])
    posted = _read_jsonl(out / "posted_videos.jsonl")
    if not posted:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    # 比べるのは動画の投稿だけ(10-23 までの画像の投稿は再生数の出方が違うので入れない)
    video_titles = [(r.get("title") or "").strip() for r in posted if (r.get("title") or "").strip()]
    aged = [p for p in _tiktok_posts(config) if now - p["published"] >= datetime.timedelta(hours=MIN_AGE_HOURS)
            and any(p["content"].startswith(t) for t in video_titles)]
    if len(aged) < 5:
        print(f"バズの確認: 比べられる投稿が少ないので見送り({len(aged)}本)")
        return None
    median = statistics.median(p["views"] for p in aged)
    line = max(BUZZ_MIN_VIEWS, median * BUZZ_RATIO)
    print(f"バズの確認: ここ2週間の再生数の中央値 {median:.0f}回 → {line:.0f}回以上をバズとみなす")

    buzzed = []
    since = now - datetime.timedelta(hours=LOOK_BACK_HOURS)

    def posted_at(rec):
        try:
            return datetime.datetime.fromisoformat(rec.get("posted_at") or "")
        except ValueError:
            return None

    for rec in [r for r in posted if posted_at(r) and posted_at(r) >= since]:
        title = (rec.get("title") or "").strip()
        match = next((p for p in aged if title and p["content"].startswith(title)), None)
        if not match:
            continue
        print(f"  {title}: {match['views']}回")
        if match["views"] >= line:
            buzzed.append((rec, match["views"]))

    # 再生数の多い順に。同じネタをもう5回作ったもの・直前の投稿と同じネタ・前回の追いかけが伸びなかったものは飛ばす
    log = _read_jsonl(out / CANDIDATE_LOG)

    def root_of(source):
        entry = next((r for r in reversed(log) if r.get("source") == source), {})
        return entry.get("buzz_root") or source, entry

    last_root, _ = root_of((posted[-1].get("source") or "").replace("\\", "/"))
    seen = set()
    for rec, views in sorted(buzzed, key=lambda x: -x[1]):
        source = (rec.get("source") or "").replace("\\", "/")
        root, entry = root_of(source)
        if root in seen:
            continue
        seen.add(root)
        followups = [r for r in log if r.get("buzz_root") == root]
        if len(followups) >= MAX_FOLLOWUPS:
            print(f"  「{rec.get('title')}」はバズっていますが、同じネタはもう{len(followups)}回作ったので飛ばします")
            continue
        if root == last_root:
            print(f"  「{rec.get('title')}」は直前の投稿と同じネタなので、今回は別のネタをはさみます")
            continue
        if followups:
            last_src = followups[-1].get("source")
            last_rec = next((r for r in reversed(posted) if (r.get("source") or "").replace("\\", "/") == last_src), {})
            t = (last_rec.get("title") or "").strip()
            m = next((p for p in aged if t and p["content"].startswith(t)), None)
            if m and m["views"] < median:
                print(f"  「{rec.get('title')}」は前回の追いかけ「{t}」が伸びなかった({m['views']}回)ので、このネタはやめます")
                continue
        # 前に同じネタで出した投稿を全部渡す(どれとも違う切り口にするため)。最初の投稿から順に
        sources = [root] + [r.get("source") for r in followups]
        texts = [Path(x).read_text(encoding="utf-8") for x in sources if x and Path(x).exists()]
        text = "\n\n----------\n\n".join(texts)[-6000:]
        print(f"  バズった投稿: {rec.get('title')}({views}回)→ 同じネタを別の切り口で作ります({len(followups) + 1}回目)")
        return {"title": rec.get("title") or "", "views": views, "root": root, "post_text": text, "entry": entry}
    return None


def _pick_same_topic(items: list[dict], buzz: dict):
    """取ってきた記事の中から、バズった投稿と同じネタ(同じ人・同じ出来事)の別の記事を選ぶ。無ければ None"""
    import anthropic

    used = buzz["entry"].get("article_url")
    pool = [i for i in items if i["news_items"][0]["url"] != used][:240]
    if not pool:
        return None
    titles = "\n".join(f"{n}. {i['keyword']}" for n, i in enumerate(pool))
    prompt = ("次の投稿がよく見られました。これと同じネタ（同じ人物・同じ出来事）を扱っている、別の記事を下の一覧から1つ選んでください。\n"
              f"【よく見られた投稿】\n{buzz['post_text'][:1200]}\n\n【記事の一覧】\n{titles}\n\n"
              "同じネタの記事が無ければ -1。返事は番号だけ。")
    r = anthropic.Anthropic().messages.create(model=PICK_MODEL, max_tokens=20,
                                              messages=[{"role": "user", "content": prompt}])
    answer = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
    try:
        n = int(answer.split()[0])
    except (ValueError, IndexError):
        return None
    return pool[n] if 0 <= n < len(pool) else None


def angle_note(buzz: dict) -> str:
    return (f"【今回の作り方】この話題は、前回の投稿「{buzz['title']}」がよく見られました。"
            "同じ話題で、前回とは違う切り口（例：別の人物や立場から見る・背景や経緯・ファンや世間の反応・今後どうなるか・意外な豆知識）で1本作ってください。"
            "これまでの投稿（下に全部あります）のどれとも同じタイトル・同じ書き出し・同じ問いかけ・同じ切り口にしないこと。記事に無い事実は作らないこと。\n\n"
            f"【これまでの投稿】\n{buzz['post_text']}")


def pick_buzz_candidate(items: list[dict], buzz: dict, extract_article_text, min_chars: int):
    """バズったネタで作るときの trend_candidate を返す(新しい記事があればそれ、無ければ前回と同じ記事)"""
    try:
        item = _pick_same_topic(items, buzz)
    except Exception as e:
        print(f"  同じネタの記事を探せませんでした: {e}")
        item = None
    if item:
        news = item["news_items"][0]
        text = extract_article_text(news["url"])
        if len(text) >= min_chars:
            print(f"  同じネタの新しい記事: {item['keyword']}")
            return {"keyword": item["keyword"], "article_url": news["url"], "article_source": news["source"],
                    "article_text": text[:3000], "buzz_root": buzz["root"], "angle_note": angle_note(buzz)}
    entry = buzz["entry"]
    print("  同じネタの新しい記事は無いので、前回と同じ記事を別の切り口で使います")
    return {"keyword": entry.get("keyword") or buzz["title"].lstrip("■"),
            "article_url": entry.get("article_url") or "", "article_source": entry.get("article_source") or "",
            "article_text": entry.get("article_text") or buzz["post_text"],
            "buzz_root": buzz["root"], "angle_note": angle_note(buzz)}
