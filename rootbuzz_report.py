#!/usr/bin/env python3
"""
依頼「ルートバズ」の TikTok アカウント(@kei_otoko_honne)の詳しいレポートを作る。
combined_report.py から呼ばれ、毎朝のフォロワーレポートと一緒に ChatWork に送る。

このアカウントは TikTok for Business 経由で Zernio(4つ目)に接続しているので、
動画ごとに 視聴時間・完全視聴率・流入元 なども取れる(投稿の24〜48時間後から、直近7日の投稿のみ)。
アカウント全体の表示回数・リーチは TikTok のどの API でも取れない。

設定: accounts/rootbuzz_kei.yaml
"""

import os
import re
import datetime

import requests

from report_followers import load_account_config

ZERNIO_API_BASE = "https://zernio.com/api/v1"
POST_DAYS = 7
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
SOURCE_LABELS = [
    ("forYou", "おすすめ"),
    ("personalProfile", "プロフィール"),
    ("search", "検索"),
    ("follow", "フォロー中"),
    ("sound", "楽曲"),
    ("directMessage", "DM"),
    ("other", "その他"),
]


def today_jst() -> datetime.date:
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).date()


def zernio_get(api_key: str, path: str, params: dict) -> dict:
    r = requests.get(
        f"{ZERNIO_API_BASE}/{path}",
        headers={"Authorization": f"Bearer {api_key}"},
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def with_delta(total, values: list, unit: str = "") -> str:
    """累計値に、日ごとの記録があれば前日との差を付ける"""
    text = f"{total}{unit}"
    points = sorted((v for v in values or [] if v.get("value") is not None), key=lambda v: v["date"])
    if len(points) >= 2:
        delta = points[-1]["value"] - points[-2]["value"]
        text += f"【前日{'+' if delta >= 0 else ''}{delta}】"
    return text


def build_account_lines(api_key: str, account_id: str) -> list[str]:
    today = today_jst()
    series = zernio_get(api_key, "analytics/tiktok/account-insights", {
        "accountId": account_id,
        "metrics": "follower_count,following_count,likes_count,video_count",
        "metricType": "time_series",
        "fromDate": (today - datetime.timedelta(days=3)).isoformat(),
        "toDate": today.isoformat(),
    }).get("metrics", {})
    moves = zernio_get(api_key, "analytics/tiktok/account-insights", {
        "accountId": account_id,
        "metrics": "followers_gained,followers_lost",
        "metricType": "time_series",
        "fromDate": (today - datetime.timedelta(days=1)).isoformat(),
        "toDate": today.isoformat(),
    }).get("metrics", {})

    def metric(name):
        m = series.get(name)
        return (m.get("total"), m.get("values", [])) if m else (None, [])

    lines = []
    total, values = metric("follower_count")
    if total is not None:
        line = f"・フォロワー：{with_delta(total, values, '人')}"
        gained = moves.get("followers_gained", {}).get("total")
        lost = moves.get("followers_lost", {}).get("total")
        if gained is not None and lost is not None:
            line += f"（増{gained}／減{lost}）"
        lines.append(line)
    total, values = metric("likes_count")
    if total is not None:
        lines.append(f"・総いいね：{with_delta(total, values)}")
    total, values = metric("video_count")
    if total is not None:
        lines.append(f"・投稿数：{with_delta(total, values, '本')}")
    total, _ = metric("following_count")
    if total is not None:
        lines.append(f"・フォロー中：{total}人")
    return lines


def post_title(content: str) -> str:
    """説明文の1行目からハッシュタグを除いたものを題名にする"""
    first = (content or "").strip().split("\n")[0]
    first = re.split(r"\s#", first)[0].strip()
    return first[:30] or "(説明文なし)"


def pct(value) -> str:
    return f"{round((value or 0) * 100, 1):g}%"


def build_post_lines(api_key: str, account_id: str) -> tuple[list[str], dict]:
    today = today_jst()
    data = zernio_get(api_key, "analytics", {
        "platform": "tiktok",
        "accountId": account_id,
        "fromDate": (today - datetime.timedelta(days=POST_DAYS)).isoformat(),
        "toDate": today.isoformat(),
        "limit": 50,
    })
    posts = sorted(data.get("posts", []), key=lambda p: p.get("publishedAt") or "", reverse=True)

    totals = {"views": 0, "likes": 0, "comments": 0, "shares": 0, "saves": 0, "follows": 0}
    lines = []
    for i, post in enumerate(posts):
        a = post.get("analytics") or {}
        for k in totals:
            totals[k] += a.get(k) or 0

        published = post.get("publishedAt") or ""
        try:
            dt = datetime.datetime.fromisoformat(published.replace("Z", "+00:00")) + datetime.timedelta(hours=9)
            date_str = f"{dt.month}/{dt.day}"
        except ValueError:
            date_str = "?"
        mark = CIRCLED[i] if i < len(CIRCLED) else f"({i + 1})"

        lines.append(f"{mark}{date_str} {post_title(post.get('content'))}")
        lines.append(
            f"　再生{a.get('views') or 0}｜いいね{a.get('likes') or 0}｜コメント{a.get('comments') or 0}"
            f"｜シェア{a.get('shares') or 0}｜保存{a.get('saves') or 0}"
        )
        avg_sec = round((a.get("igReelsAvgWatchTime") or 0) / 1000, 1)
        retention = (a.get("retentionCurve") or {}).get("2")
        detail = f"　平均視聴{avg_sec:g}秒｜最後まで視聴{pct(a.get('completionRate'))}"
        if retention is not None:
            detail += f"｜2秒後も視聴{pct(retention)}"
        lines.append(detail)
        sources = a.get("impressionSources") or {}
        if any(sources.values()):
            parts = [f"{label}{pct(sources.get(key))}" for key, label in SOURCE_LABELS
                     if key in ("forYou", "personalProfile", "search") or sources.get(key)]
            lines.append("　流入：" + "・".join(parts))
        audience = a.get("audienceTypes") or {}
        viewer = f"フォロワー外{pct(audience.get('nonFollower'))}｜" if any(audience.values()) else ""
        lines.append(
            f"　視聴者：{viewer}新規フォロー{a.get('follows') or 0}｜プロフィール表示{a.get('profileViews') or 0}"
        )
    return lines, totals


def build_rootbuzz_report(config_path: str, timestamp_str: str) -> str:
    config = load_account_config(config_path)
    label = config.get("chatwork_label", config.get("display_name"))
    api_key = os.environ.get(config["zernio_api_key_env"])
    account_id = os.environ.get(config["zernio_account_id_env"])

    body = [
        "＝＝＝＝＝ルートバズレポートです＝＝＝＝＝",
        f"{timestamp_str}現在",
        f"■アカウント全体（{label}）",
    ]
    if not api_key or not account_id:
        body.append("・未接続（GitHub Secrets を確認）")
    else:
        try:
            body.extend(build_account_lines(api_key, account_id))
        except Exception as e:
            print(f"エラー(ルートバズ アカウント全体): {e}")
            body.append("・取得エラー")

        body.append("")
        body.append(f"■直近{POST_DAYS}日の投稿（新しい順）")
        try:
            post_lines, totals = build_post_lines(api_key, account_id)
            body.extend(post_lines or ["・投稿なし"])
            body.append("")
            body.append(f"■直近{POST_DAYS}日の合計")
            body.append(
                f"・再生{totals['views']}｜いいね{totals['likes']}｜コメント{totals['comments']}"
                f"｜シェア{totals['shares']}｜保存{totals['saves']}｜新規フォロー{totals['follows']}"
            )
            body.append("※動画の詳しい数字は、TikTokの集計に投稿から1〜2日かかります")
        except Exception as e:
            print(f"エラー(ルートバズ 投稿): {e}")
            body.append("・取得エラー")

    body.append("＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝")
    return "\n".join(body)
