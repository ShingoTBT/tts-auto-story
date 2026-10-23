#!/usr/bin/env python3
"""
TikTok の自分の投稿に付いたコメントに、いいねと絵文字1つの返信を付ける(Zernio の inbox。2026-10-23〜)。
Xserver(alley-oop)の cron から1時間に1回 comment_responder.yaml が起動される。

使い方:
    python reply_comments_tiktok.py accounts/account5_tiktok_engagement.yaml [...]

- 対象: 直近 DAYS 日に投稿した動画・画像のコメント(TikTok for Business でつないだアカウントだけ使える)
- 自分のコメント・自分がすでに返信したコメントには付けない。対応したコメントは
  <output_dir>/comment_replies.jsonl に記録して、二度付けしない
- 機械的に見えないよう、1件ごとに数秒あけ、1回に付けるのは MAX_PER_RUN 件まで
必要な環境変数: アカウント設定の zernio_api_key_env / zernio_account_id_env(zernio_account_id を直接書いてもよい)
"""
import datetime
import json
import os
import random
import sys
import time
from pathlib import Path

import requests
import yaml

API = "https://zernio.com/api/v1"
DAYS = 7
MAX_PER_RUN = 30
# どんな内容のコメントにも合う、前向きな絵文字(ユーザー希望「顔文字より絵文字」10-23)
EMOJIS = ["😊", "🥹", "🙏", "✨", "🫶", "👀", "💡", "🙌"]


def call(method, path, key, **kw):
    r = requests.request(method, f"{API}/{path}", headers={"Authorization": f"Bearer {key}"}, timeout=60, **kw)
    try:
        body = r.json()
    except ValueError:
        body = {"text": r.text[:200]}
    return r.status_code, body


def run_account(config_path):
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    label = cfg.get("chatwork_label", cfg.get("display_name"))
    key = os.environ.get(cfg.get("zernio_api_key_env", "ZERNIO_API_KEY"))
    acc = cfg.get("zernio_account_id") or os.environ.get(cfg.get("zernio_account_id_env", ""))
    if not key or not acc:
        print(f"[{label}] 合鍵かアカウントIDが無いので飛ばします")
        return
    log = Path(cfg.get("output_dir") or f"outputs/{cfg['account_name']}") / "comment_replies.jsonl"
    done = set()
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["comment_id"])
            except Exception:
                pass

    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DAYS)
    st, data = call("GET", "analytics", key, params={"platform": "tiktok", "accountId": acc, "limit": 50})
    posts = []
    for p in data.get("posts", []):
        try:
            when = datetime.datetime.fromisoformat((p.get("publishedAt") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        pid = next((pl.get("platformPostId") for pl in p.get("platforms", []) if pl.get("platformPostId")), None)
        if pid and when >= since:
            posts.append((when, pid, (p.get("content") or "")[:30]))
    # analytics には投稿の 24〜48 時間後からしか出ないので、コメントの付いた投稿の一覧も足す(出したばかりの動画のコメントも拾う)
    known = {pid for _, pid, _ in posts}
    st, data = call("GET", "inbox/comments", key, params={"accountId": acc})
    for p in data.get("data", []) if st == 200 else []:
        try:
            when = datetime.datetime.fromisoformat((p.get("createdTime") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if p.get("id") and p["id"] not in known and when >= since and p.get("commentCount"):
            posts.append((when, p["id"], (p.get("content") or "")[:30]))
            known.add(p["id"])
    print(f"[{label}] 直近{DAYS}日の投稿: {len(posts)}本")

    count = 0
    for when, pid, title in sorted(posts, reverse=True):
        st, data = call("GET", f"inbox/comments/{pid}", key, params={"accountId": acc, "limit": 100})
        if st != 200:
            print(f"  コメントを読めませんでした {pid}: {st} {str(data)[:120]}")
            continue
        for c in data.get("comments", []):
            cid = c.get("id")
            if not cid or cid in done or (c.get("from") or {}).get("isOwner"):
                continue
            if any((r.get("from") or {}).get("isOwner") for r in c.get("replies") or []):
                done.add(cid)
                continue   # すでに自分が返信している
            if count >= MAX_PER_RUN:
                print(f"  1回の上限({MAX_PER_RUN}件)に達したので、残りは次の回に")
                return
            liked = c.get("isLiked")
            if not liked:
                st_l, _ = call("POST", f"inbox/comments/{pid}/{cid}/like", key, json={"accountId": acc})
                liked = st_l == 200
            emoji = random.choice(EMOJIS)
            # Idempotency-Key: やり直しても同じコメントに二重に返信しない
            r = requests.post(f"{API}/inbox/comments/{pid}", timeout=60,
                              headers={"Authorization": f"Bearer {key}", "Idempotency-Key": f"emoji-{cid}"},
                              json={"accountId": acc, "message": emoji, "commentId": cid})
            ok = r.status_code == 200
            print(f"  {title!r} のコメント {cid}: いいね{'○' if liked else '×'} 返信{emoji}{'○' if ok else '×'}")
            if ok:
                log.parent.mkdir(parents=True, exist_ok=True)
                with open(log, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds"),
                                        "post_id": pid, "comment_id": cid, "emoji": emoji, "liked": bool(liked)}, ensure_ascii=False) + "\n")
                done.add(cid)
                count += 1
            time.sleep(random.uniform(3, 8))
    print(f"[{label}] 今回対応したコメント: {count}件")


def main():
    for config_path in sys.argv[1:]:
        run_account(config_path)


if __name__ == "__main__":
    main()
