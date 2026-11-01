#!/usr/bin/env python3
"""
ニュース(アカウント2・芸能スポーツの回)用: X の日本のトレンドから「SNSで話題」のネタを作る(2026-11-01〜 ユーザー希望)

- X 公式 API(従量課金。console.x.com、開発者アカウント alleyoop-trend-news。月の上限 $25)
  - 日本のトレンド GET /2/trends/by/woeid/23424856($0.010/回)
  - そのトレンドの投稿を検索 GET /2/tweets/search/recent(投稿1件 $0.005。1回 POSTS_TO_READ 件)
- 毎回、ニュース記事のいちばん良い候補と X のトレンドを AI が比べ、話題性の高いほうを使う(ユーザー選択「B」)
- ニュース以外の話題(流行り言葉・ネットで盛り上がっていることなど)も対象(ユーザー選択「1」)
- X の規約に合わせて: 投稿の文はそのまま使わない・一般の人を特定しない・「X」ではなく「SNSで話題」と書く(ユーザー了承)
  API で取ったネタはこの自動投稿だけに使う(会員サイトなどには使わない。ユーザー了承 11-01)

使い方(確認用): python x_trends.py accounts/account2_news.yaml   … トレンドと、選ばれる候補を表示するだけ
"""

import json
import os
import re
import sys
import urllib.parse

import requests

X_API = "https://api.x.com/2"
JAPAN_WOEID = 23424856
POSTS_TO_READ = 20          # 1回 約 $0.10
PICK_MODEL = "claude-sonnet-4-6"

X_NOTE = ("【今回の作り方】このネタはニュース記事ではなく、SNSで今まさに話題になっている言葉です。"
          "下の「SNSの投稿」は、何が話題になっているか・どんな反応があるかを知るための材料です。"
          "多くの投稿に共通している話題・反応を中心に書き、1つの投稿だけにある少数の意見や、話の中心から外れた話は入れないこと。"
          "投稿の文をそのまま使わず、全部自分の言葉で書くこと。一般の人の名前・アカウント名・個人が特定できることは書かないこと。"
          "投稿に書かれているだけで確かめられないことは事実として断定せず、「〜とSNSで話題に」「〜という声も」のように書くこと。"
          "「X」「Twitter」「ツイート」という言葉は使わず「SNS」と書くこと。")


def _headers():
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    if not token:
        raise RuntimeError("X_BEARER_TOKEN がありません")
    return {"Authorization": f"Bearer {token}"}


def fetch_trends() -> list[dict]:
    """日本のトレンド。[{name, count}]"""
    r = requests.get(f"{X_API}/trends/by/woeid/{JAPAN_WOEID}", headers=_headers(), timeout=30)
    if not r.ok:
        tok = os.environ.get("X_BEARER_TOKEN", "").strip()
        # 合鍵そのものは出さない。形だけ(長さ・先頭が AAAA か)を出して、貼り間違いを見分ける
        print(f"  X API エラー {r.status_code}: {r.text[:300]} (合鍵の長さ {len(tok)}・先頭AAAA {tok.startswith('AAAA')})")
    r.raise_for_status()
    out = []
    for t in r.json().get("data", []):
        name = (t.get("trend_name") or t.get("name") or "").strip()
        if name:
            out.append({"name": name, "count": t.get("tweet_count") or t.get("post_count") or 0})
    return out


def search_posts(name: str) -> list[str]:
    """そのトレンドの日本語の投稿(リポスト・返信は除く)。本文だけ、アカウント名と URL は消す"""
    q = f'"{name}"' if " " in name and not name.startswith("#") else name
    query = f"{q} lang:ja -is:retweet -is:reply"
    r = requests.get(f"{X_API}/tweets/search/recent", headers=_headers(), timeout=30, params={
        "query": query, "max_results": POSTS_TO_READ, "sort_order": "relevancy",
        "tweet.fields": "public_metrics",
    })
    r.raise_for_status()
    posts = []
    for p in r.json().get("data", []):
        text = re.sub(r"@\w+", "", p.get("text") or "")
        text = re.sub(r"https?://\S+", "", text).strip()
        if text:
            m = p.get("public_metrics") or {}
            posts.append((m.get("like_count", 0) + m.get("retweet_count", 0) * 2, text))
    posts.sort(key=lambda x: -x[0])
    return [t for _, t in posts]


def choose(news_items: list[dict], trends: list[dict], recent_posts: list[str], topic: str, force_x=False):
    """ニュースのいちばん良い候補(先頭数件)と X のトレンドを比べる。X を使うならトレンドの名前、ニュースなら None"""
    import anthropic
    from generate import claude_extra_params

    news = "\n".join(f"- {i['keyword']}" for i in news_items[:5]) or "(なし)"
    trend_list = "\n".join(f"{n}: {t['name']}" + (f"(投稿数 {t['count']})" if t["count"] else "")
                           for n, t in enumerate(trends[:30]))
    recent = "\n".join(f"- {t}" for t in recent_posts[:30]) or "(なし)"
    prompt = (
        "TikTok のニュースアカウント（主に「" + topic + "」、ほかにネットで話題のことも扱う）で、次に1本投稿します。\n"
        "下の「ニュース記事の候補」と「SNSのトレンド」を比べて、TikTok でバズりやすい（見られて、コメントが集まりやすい）ほうを選んでください。\n"
        "バズりやすいネタ: 賛否が分かれる（許せる？許せない？・アリ？ナシ？で聞ける）、思わずツッコミたくなる・驚きがある、"
        "誰もが知っている有名人やブランドが関わる、自分の体験や意見を言いたくなる。\n"
        "バズりにくいネタ（選ばない）: 季節の決まり文句だけのもの（「あと2ヶ月」「11月」など）、番組名・定期イベント名だけで中身が分からないもの、ファンの少ないジャンルの内輪の話。\n"
        "ただし、人気アイドル（STARTO・旧ジャニーズ系、K-POP、坂道グループなど）や人気アーティストのファン向けの話題は、ファンの数が多いのでバズりやすいネタとして選んでよい（11-01 ユーザー希望）。\n"
        "SNSのトレンドを選んでよいのは次をすべて満たすものだけ:\n"
        "- 芸能・スポーツ・エンタメ、または誰でも話に乗れるネットの流行り（流行り言葉・季節の話題・盛り上がっている出来事など）\n"
        "- 政治・選挙・宗教・事件・事故・訃報・病気・災害・誰かが責められている話（炎上・不祥事・謝罪）ではない\n"
        "- 一般の人個人が話の中心ではない\n"
        "- 意味が分からない単語だけのもの（略語・ゲームのイベント名だけなど）ではない。人気アイドル・アーティストのファンの間の話題（メンバーの愛称・ライブ・番組出演など）は、何の話か投稿から分かるならOK\n"
        "- 直近の投稿と同じ人物・出来事ではない\n"
        + ("今回は確認のため、条件を満たすトレンドがあれば必ずトレンドから選んでください。\n" if force_x else "")
        + "回答は JSON 1つだけ: トレンドを選ぶなら {\"pick\": \"trend\", \"index\": 番号}、ニュース記事のほうがよければ {\"pick\": \"news\"}。説明は書かない。\n\n"
        f"【ニュース記事の候補（良い順）】\n{news}\n\n【SNSのトレンド】\n{trend_list}\n\n【直近の投稿】\n{recent}"
    )
    r = anthropic.Anthropic().messages.create(model=PICK_MODEL, max_tokens=100, **claude_extra_params(PICK_MODEL),
                                              messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    ans = json.loads(text[text.index("{"):text.rindex("}") + 1])
    if ans.get("pick") == "trend":
        idx = int(ans["index"])
        if 0 <= idx < len(trends):
            return trends[idx]["name"]
    return None


def build_candidate(name: str) -> dict | None:
    posts = search_posts(name)
    print(f"  SNSの投稿を {len(posts)} 件読みました")
    if len(posts) < 5:
        return None
    body = "\n".join(f"・{p[:280]}" for p in posts)
    return {
        "keyword": name,
        "article_url": "https://x.com/search?q=" + urllib.parse.quote(name),
        "article_source": "X トレンド",
        "article_text": (f"【SNSで話題の言葉】{name}\n\n【SNSの投稿（材料。そのまま使わない・個人を特定しない）】\n{body}")[:4000],
        "x_note": X_NOTE,
    }


def try_x_candidate(news_items, recent_posts, recent_keywords, topic, force_x=False):
    """X のトレンドのほうが良ければ trend_candidate を返す。ニュースのほうが良い・失敗したときは None"""
    trends = [t for t in fetch_trends() if t["name"] not in recent_keywords]
    print(f"X の日本のトレンド: {len(trends)}件(例: {[t['name'] for t in trends[:8]]})")
    if not trends:
        return None
    name = choose(news_items, trends, recent_posts, topic, force_x)
    if not name:
        print("  → 今回はニュース記事のほうを使います")
        return None
    print(f"  → X のトレンド「{name}」を使います")
    return build_candidate(name)


if __name__ == "__main__":
    import yaml
    cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
    for t in fetch_trends()[:30]:
        print(t)
