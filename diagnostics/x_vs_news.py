"""一時: ニュース(アカウント2)の X ネタとニュース記事ネタの再生数を比べる(調べ終わったら消す)"""
import datetime, json, os, statistics
from pathlib import Path
import requests

out = Path("outputs/account2_news")
posted = [json.loads(l) for l in (out / "posted_videos.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
cands = {}
for l in (out / "news_candidates.jsonl").read_text(encoding="utf-8").splitlines():
    try:
        r = json.loads(l)
        cands[r["source"]] = r
    except Exception:
        pass
key = os.environ["ZERNIO_API_KEY"]; acc = os.environ["ZERNIO_TIKTOK_ACCOUNT_ID_2"]
today = datetime.date.today()
r = requests.get("https://zernio.com/api/v1/analytics", headers={"Authorization": f"Bearer {key}"}, timeout=60,
                 params={"platform": "tiktok", "accountId": acc, "limit": 100,
                         "fromDate": (today - datetime.timedelta(days=14)).isoformat(),
                         "toDate": (today + datetime.timedelta(days=1)).isoformat()})
posts = r.json().get("posts", [])
lines = []
groups = {}
for rec in posted:
    if (rec.get("posted_at") or "") < "2026-10-28":
        continue
    t = (rec.get("title") or "").strip()
    m = next((p for p in posts if t and (p.get("content") or "").startswith(t)), None)
    if not m:
        continue
    a = m.get("analytics") or {}
    c = cands.get((rec.get("source") or "").replace("\\", "/"), {})
    src = c.get("article_source") or ""
    hour = int(rec["posted_at"][11:13])
    if src.startswith("X"):
        kind = "X"
    elif c.get("buzz_root"):
        kind = "buzz"
    elif hour in (6, 11):
        kind = "explain"
    else:
        kind = "news"
    if rec["posted_at"] >= "2026-11-01T11":
        groups.setdefault(kind, []).append(a.get("views") or 0)
    lines.append(f"{rec['posted_at'][:16]}\t{kind}\t{a.get('views')}\tlike{a.get('likes')}\tcom{a.get('comments')}\t{t}\t{c.get('keyword','')[:40]}")
lines.append("")
for k, v in groups.items():
    lines.append(f"{k}: {len(v)}本 中央値{statistics.median(v):.0f} 平均{statistics.mean(v):.0f}")
Path("diagnostics/x_vs_news_result.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
