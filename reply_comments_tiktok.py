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
# 1アカウントにかけてよい秒数。2026-11-28 回が20分の時間切れで何度も打ち切られ、最後の順番のメイ（ルートバズ）に
# 6時間返信が付かなかった → どのアカウントも時間を使い切らないよう区切り、残りは次の回に（新しい投稿から順に見る）
MAX_SEC_PER_ACCOUNT = 120
# どんな内容のコメントにも合う、前向きな絵文字(ユーザー希望「顔文字より絵文字」10-23)
EMOJIS = ["😊", "🥹", "🙏", "✨", "🫶", "👀", "💡", "🙌"]


def wait_429(r):
    """回数制限（1分60回）にかかったら、言われた秒数だけ待つ。待ったら True（2026-11-07 メイの投稿46本で読み残しが出た）"""
    if r.status_code != 429:
        return False
    try:
        sec = float((r.json().get("details") or {}).get("retryAfter") or 10)
    except Exception:
        sec = 10
    time.sleep(min(sec, 60) + 1)
    return True


def request_retry(method, url, **kw):
    """Zernio との接続が一瞬切れた（ConnectionResetError など）ときは、少し待って3回までやり直す。
    11-13・11-16 に全アカウントで接続が切られ、回ごと失敗して失敗メールが届いていた（次の回では直っていた）。
    返信の送信もやり直してよい：同じ Idempotency-Key なら Zernio は二重に投稿しない"""
    for i, wait in enumerate((5, 20, 60, None)):
        try:
            return requests.request(method, url, **kw)
        except (requests.ConnectionError, requests.Timeout) as e:
            if wait is None:
                raise
            print(f"  接続が切れたので {wait} 秒待ってやり直します（{i + 1}回目）: {type(e).__name__}")
            time.sleep(wait)


_last = [0.0]


def call(method, path, key, **kw):
    # 1分60回の決まりに収まるよう、呼ぶ間を1.1秒あける
    for _ in range(6):
        gap = time.time() - _last[0]
        if gap < 1.1:
            time.sleep(1.1 - gap)
        _last[0] = time.time()
        r = request_retry(method, f"{API}/{path}", headers={"Authorization": f"Bearer {key}"}, timeout=60, **kw)
        if not wait_429(r):
            break
    try:
        body = r.json()
    except ValueError:
        body = {"text": r.text[:200]}
    return r.status_code, body


def owner_replied(pid, comment, key, acc):
    """このコメントに自分の返信が付いているか。True=付いている / False=確かに付いていない / None=確かめられない
    TikTok は一覧の各コメントに返信を3件までしか載せない（Zernio の仕様）。返信が多いコメントでは自分の返信が
    載らず「付いていない」と誤って判断し、重ねて送っていた（2026-11-15 メイで同じコメントに3つ）。
    → 載っていなければ、そのコメントの返信を全部読み出して（commentId 指定）確かめる"""
    inline = comment.get("replies") or []
    if any((r.get("from") or {}).get("isOwner") for r in inline):
        return True
    count = comment.get("replyCount")
    if count is not None and int(count) <= len(inline):
        return False   # 返信は全部載っていて、その中に自分の返信は無い
    count = int(count or 0)
    replies, cur = [], None
    for _ in range(10):
        st, data = call("GET", f"inbox/comments/{pid}", key,
                        params={"accountId": acc, "commentId": comment["id"], "limit": 100, **({"cursor": cur} if cur else {})})
        if st != 200:
            return None
        replies += data.get("comments", [])
        pg = data.get("pagination") or {}
        cur = pg.get("cursor") or pg.get("nextCursor")
        if not pg.get("hasMore") or not cur:
            break
    if any((r.get("from") or {}).get("isOwner") for r in replies):
        return True
    # 読み出せた数が返信の数より少ないときは、読み残しがあるかもしれないので「確かめられない」
    return False if len(replies) >= count else None


def reply_id(r):
    """送った返信の ID（記録用）"""
    try:
        return ((r.json() or {}).get("data") or {}).get("commentId")
    except ValueError:
        return None


def pick_reply(comment, cfg):
    """返す絵文字。アカウント設定の reply_rules（match の言葉がコメントに入っていたら reply を返す）を先に見る
    2026-10-30 ユーザー：金運すずの「〇〇しますように」などの願いごとには、手を合わせる「🙏✨」を"""
    text = comment.get("text") or comment.get("message") or ""
    for rule in cfg.get("reply_rules") or []:
        if any(w in text for w in rule.get("match", [])):
            r = rule["reply"]
            return random.choice(r) if isinstance(r, list) else r
    # アカウント設定の emojis があれば、その中からランダム（2026-11-06 メイ＝ハート系）
    return random.choice(cfg.get("emojis") or EMOJIS)


def run_account(config_path):
    started = time.time()
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    label = cfg.get("chatwork_label", cfg.get("display_name"))
    key = (os.environ.get(cfg.get("zernio_api_key_env", "ZERNIO_API_KEY")) or "").strip()  # 登録時に末尾の改行が入っても動くように（2026-11-06 メイで発生）
    acc = cfg.get("zernio_account_id") or os.environ.get(cfg.get("zernio_account_id_env", ""))
    if not key or not acc:
        print(f"[{label}] 合鍵かアカウントIDが無いので飛ばします")
        return
    log = Path(cfg.get("output_dir") or f"outputs/{cfg['account_name']}") / "comment_replies.jsonl"
    # 返信を送った記録（コメントごとに送った時刻の一覧）。
    # 2026-11-07 メイで、送信は成功（200）と返ったのに返信が付いていないことが2件あった → 送り直しを入れた。
    # ところが一覧には返信が3件までしか載らず、付いている返信を「無い」と見て重ねて送っていた（11-15 ユーザー「重複はマズイ」）。
    # → 送り直すのは、そのコメントの返信を全部読み出して、自分の返信が確かに無いと分かったときだけ（送ってから50分〜6時間・3回まで）。
    #   確かめられないときは送らない（重複よりは、次の回に回すほうが良い）
    tries = {}
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                tries.setdefault(d["comment_id"], []).append(datetime.datetime.fromisoformat(d["at"]))
            except Exception:
                pass
    done = set(tries)
    now = datetime.datetime.now(datetime.timezone.utc)

    # アカウント設定の days で、何日前までの投稿を見るか変えられる（メイ＝昔の投稿にも今コメントが付くので長く。2026-11-05）
    days = int(cfg.get("days", DAYS))
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    # comments_since（日本時間の日付）より前に書かれたコメントには付けない（昔のコメントへの一斉返信を避ける）
    c_since = cfg.get("comments_since")
    c_since = datetime.datetime.fromisoformat(str(c_since)).replace(tzinfo=datetime.timezone(datetime.timedelta(hours=9))) if c_since else None
    st, data = call("GET", "analytics", key, params={"platform": "tiktok", "accountId": acc, "limit": 100})
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
    # 2026-11-07 ページ送りも読む（1ページ目だけだと、投稿やコメントが多いときに取りこぼす）
    inbox, cur = [], None
    for _ in range(10):
        st, data = call("GET", "inbox/comments", key, params={"accountId": acc, **({"cursor": cur} if cur else {})})
        if st != 200:
            break
        inbox += data.get("data", [])
        cur = (data.get("pagination") or {}).get("nextCursor") or (data.get("pagination") or {}).get("cursor")
        if not (data.get("pagination") or {}).get("hasMore") or not cur:
            break
    for p in inbox:
        try:
            when = datetime.datetime.fromisoformat((p.get("createdTime") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if p.get("id") and p["id"] not in known and when >= since and p.get("commentCount"):
            posts.append((when, p["id"], (p.get("content") or "")[:30]))
            known.add(p["id"])
    print(f"[{label}] 直近{days}日の投稿: {len(posts)}本")

    count = 0
    for when, pid, title in sorted(posts, reverse=True):
        if time.time() - started > int(cfg.get("max_seconds", MAX_SEC_PER_ACCOUNT)):
            print(f"  {int(time.time() - started)}秒たったので、残りの投稿は次の回に（ほかのアカウントの時間を残すため）")
            break
        comments, cur, st = [], None, 200
        for _ in range(10):
            st, data = call("GET", f"inbox/comments/{pid}", key, params={"accountId": acc, "limit": 100, **({"cursor": cur} if cur else {})})
            if st != 200:
                break
            comments += data.get("comments", [])
            pg = data.get("pagination") or {}
            cur = pg.get("cursor") or pg.get("nextCursor")
            if not pg.get("hasMore") or not cur:
                break
        if st != 200 and not comments:
            print(f"  コメントを読めませんでした {pid}: {st} {str(data)[:120]}")
            continue
        for c in comments:
            cid = c.get("id")
            if not cid or (c.get("from") or {}).get("isOwner"):
                continue
            if c_since:
                try:
                    if datetime.datetime.fromisoformat((c.get("createdTime") or "").replace("Z", "+00:00")) < c_since:
                        continue
                except ValueError:
                    pass
            retry = 0
            if cid in done:
                # 前の回で送った（記録あり）。送ってから50分〜6時間のあいだに、付いていないと確かめられたときだけ送り直す
                # （6時間より前のものまで毎回確かめると、何百件も読みに行って重くなるため）
                t = tries.get(cid) or []
                if not t or len(t) >= 3 or not (datetime.timedelta(minutes=50) <= now - max(t) <= datetime.timedelta(hours=6)):
                    continue
                has = owner_replied(pid, c, key, acc)
                if has is not False:
                    continue
                retry = len(t)
                print(f"  {cid}: 返信を全部確かめても自分の返信が無いので送り直します（{retry}回目）")
            else:
                has = owner_replied(pid, c, key, acc)
                if has is True:
                    done.add(cid)
                    continue   # すでに自分が返信している
                if has is None:
                    print(f"  {cid}: 自分の返信があるか確かめられないので、今回は送りません")
                    continue
            if count >= MAX_PER_RUN:
                print(f"  1回の上限({MAX_PER_RUN}件)に達したので、残りは次の回に")
                return
            liked = c.get("isLiked")
            if not liked:
                st_l, _ = call("POST", f"inbox/comments/{pid}/{cid}/like", key, json={"accountId": acc})
                liked = st_l == 200
            emoji = pick_reply(c, cfg)
            # Idempotency-Key: やり直しても同じコメントに二重に返信しない
            for _ in range(4):
                r = request_retry("POST", f"{API}/inbox/comments/{pid}", timeout=60,
                                  headers={"Authorization": f"Bearer {key}", "Idempotency-Key": f"emoji-{cid}" + (f"-r{retry}" if retry else "")},
                                  json={"accountId": acc, "message": emoji, "commentId": cid})
                if not wait_429(r):
                    break
            ok = r.status_code == 200
            print(f"  {title!r} のコメント {cid}: いいね{'○' if liked else '×'} 返信{emoji}{'○' if ok else '×'}")
            if ok:
                log.parent.mkdir(parents=True, exist_ok=True)
                with open(log, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds"),
                                        "post_id": pid, "comment_id": cid, "emoji": emoji, "liked": bool(liked),
                                        "reply_id": reply_id(r), **({"retry": retry} if retry else {})}, ensure_ascii=False) + "\n")
                done.add(cid)
                count += 1
            time.sleep(random.uniform(3, 8))
    print(f"[{label}] 今回対応したコメント: {count}件")


def main():
    # 11-04 1つのアカウントで Zernio が一時的にエラーを返すと、残りのアカウントも止まって失敗メールになった。
    # アカウントごとに続け、全部だめだったときだけ失敗にする(1時間後の次の回で取り戻せるため)
    failed = []
    for config_path in sys.argv[1:]:
        try:
            run_account(config_path)
        except Exception as e:
            print(f"[{config_path}] エラーで飛ばしました(次の回にやり直します): {type(e).__name__}: {e}")
            failed.append(config_path)
    if failed and len(failed) == len(sys.argv[1:]):
        sys.exit(1)


if __name__ == "__main__":
    main()
