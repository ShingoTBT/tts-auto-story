"""@kinoteck（TTS分析の依頼）の Zernio analytics を1日1回 data/kinoteck/ に保存する。

zernio5 のキーは環境変数 ZERNIO_API_KEY_KINUN（GitHub Secrets）から読む。
Zernio は直近の投稿の数字しか返さないので、毎日ためて週ごとの分析に使う。
保存先：data/kinoteck/YYYY-MM-DD.json（日本時間の日付。同じ日に2回動いたら上書き）
"""
import datetime
import json
import os
import pathlib

import requests

ZERNIO_API_BASE = "https://zernio.com/api/v1"
ACCOUNT_ID = "6ae44b7dbb87bb272f78db79"  # @kinoteck（zernio5）
OUT_DIR = pathlib.Path(__file__).resolve().parent / "data" / "kinoteck"
JST = datetime.timezone(datetime.timedelta(hours=9))


def zernio_get(api_key: str, path: str, params: dict) -> dict:
    resp = requests.get(
        f"{ZERNIO_API_BASE}/{path}",
        headers={"Authorization": f"Bearer {api_key}"},
        params=params,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    api_key = os.environ["ZERNIO_API_KEY_KINUN"]
    now = datetime.datetime.now(JST)
    data = {
        "saved_at": now.isoformat(timespec="seconds"),
        "analytics": zernio_get(api_key, "analytics", {"platform": "tiktok", "accountId": ACCOUNT_ID, "limit": 100}),
        "account_insights": zernio_get(api_key, "analytics/tiktok/account-insights", {"accountId": ACCOUNT_ID}),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{now.date().isoformat()}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {out} posts={len(data['analytics'].get('posts', []))}")


if __name__ == "__main__":
    main()
