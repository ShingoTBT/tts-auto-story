#!/usr/bin/env python3
"""
make_story_video.py が作った動画(完成.mp4)を Zernio 経由で TikTok に直接投稿する(曲付き・完全自動)。
2026-10-23〜 アカウント2・5・6 は画像ではなく動画で投稿する。

使い方:
    python post_video_to_tiktok.py <account_config.yaml> <source_text_file.txt> <動画フォルダ>

- 曲: TikTok の商用曲(Commercial Music Library・日本)から、アカウント設定 video.music_genres に合う曲を選ぶ
  (TikTok for Business でつないだアカウントだけ使える。直前に使った曲は避ける)
- 説明文: 「■タイトル」＋改行＋ハッシュタグ
- 結果は <output_dir>/posted_videos.jsonl に1行ずつ残す(声が ElevenLabs か VOICEVOX かも。毎朝のレポートで使う)

必要な環境変数: アカウント設定の zernio_api_key_env / zernio_account_id_env
"""

import json
import os
import random
import sys
import datetime
from pathlib import Path

import requests
import yaml

ZERNIO_API_BASE = "https://zernio.com/api/v1"
MUSIC_VOLUME = 8        # 曲の音量(0〜100)。ナレーションが主役なので小さく(依頼「ルートバズ」で 15 は大きいと言われた)
VOICE_VOLUME = 100      # 動画そのものの音(ナレーション・効果音)。曲を付けると既定で 0 になるので必ず指定する


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def extract_title_and_hashtags(source_text: str) -> tuple[str, str]:
    lines = [l for l in source_text.split("\n") if l.strip() != ""]
    title = lines[0].strip() if lines else ""
    hashtag_lines = [l for l in lines if l.strip().startswith("#")]
    hashtags = hashtag_lines[0].strip() if hashtag_lines else ""
    return title, hashtags


def upload(headers: dict, path: Path, content_type: str, name: str) -> str:
    pre = requests.post(f"{ZERNIO_API_BASE}/media/presign", headers=headers, timeout=30,
                        json={"filename": name, "contentType": content_type, "size": path.stat().st_size})
    pre.raise_for_status()
    pre = pre.json()
    with open(path, "rb") as f:
        requests.put(pre["uploadUrl"], data=f, headers={"Content-Type": content_type}, timeout=600).raise_for_status()
    return pre["publicUrl"]


def recent_music(log_path: Path, n: int = 5) -> set:
    if not log_path.exists():
        return set()
    used = []
    for line in log_path.read_text(encoding="utf-8").splitlines()[-n:]:
        try:
            used.append(json.loads(line).get("music_id"))
        except Exception:
            pass
    return set(used)


def pick_music(headers: dict, account_id: str, genres: list, video_sec: float, avoid: set, exclude_words=()):
    """商用曲から、ジャンルが合う曲を選ぶ(曲名に exclude_words を含むものは除く)。合う曲が無ければ上位の曲から選ぶ"""
    r = requests.get(f"{ZERNIO_API_BASE}/accounts/{account_id}/tiktok/commercial-music",
                     params={"countryCode": "JP"}, headers=headers, timeout=60)
    r.raise_for_status()
    tracks = [t for t in r.json().get("tracks", []) if (t.get("clip") or {}).get("id")]
    want = set(genres or [])
    ng = [w.lower() for w in exclude_words or []]
    tracks = [t for t in tracks if not any(w in (t.get("name") or "").lower() for w in ng)]
    match = [t for t in tracks if want & set(t.get("genres") or [])] or tracks[:30]
    long_enough = [t for t in match if (t["clip"].get("durationSec") or 0) >= video_sec] or match
    fresh = [t for t in long_enough if t["clip"]["id"] not in avoid] or long_enough
    t = random.choice(fresh[:20])
    return t["clip"]["id"], f"{t.get('name') or ''} / {t.get('artist') or ''}"


def video_seconds(path: Path) -> float:
    import subprocess
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 60.0


def main():
    if len(sys.argv) < 4:
        print("使い方: python post_video_to_tiktok.py <account_config.yaml> <source_text_file.txt> <動画フォルダ>")
        sys.exit(1)
    config = load_account_config(sys.argv[1])
    source_text_path = Path(sys.argv[2])
    folder = Path(sys.argv[3])
    api_key = os.environ[config.get("zernio_api_key_env", "ZERNIO_API_KEY")]
    account_id = os.environ[config.get("zernio_account_id_env", "ZERNIO_TIKTOK_ACCOUNT_ID")]
    headers = {"Authorization": f"Bearer {api_key}"}

    title, hashtags = extract_title_and_hashtags(source_text_path.read_text(encoding="utf-8"))
    video = folder / "完成.mp4"
    voice = {}
    if (folder / "voice.json").exists():
        voice = json.loads((folder / "voice.json").read_text(encoding="utf-8"))
    log_path = Path(config["output_dir"]) / "posted_videos.jsonl"

    try:
        vcfg = config.get("video", {})
        music_id, music_name = pick_music(headers, account_id, vcfg.get("music_genres"), video_seconds(video),
                                          recent_music(log_path), vcfg.get("music_exclude_words"))
        print("曲:", music_name, music_id)
        stem = source_text_path.stem
        video_url = upload(headers, video, "video/mp4", f"{config['account_name']}_{stem}.mp4")
        settings = {
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "allow_duet": False,
            "allow_stitch": False,
            "content_preview_confirmed": True,
            "express_consent_given": True,
            "musicSoundInfo": {"musicSoundId": str(music_id), "musicSoundVolume": MUSIC_VOLUME},
            "videoOriginalSoundVolume": VOICE_VOLUME,
            "video_cover_timestamp_ms": 500,
        }
        thumb = folder / "サムネ.png"
        if thumb.exists():
            settings["video_cover_image_url"] = upload(headers, thumb, "image/png", f"{config['account_name']}_{stem}_cover.png")
        payload = {
            "content": f"{title}\n{hashtags}",
            "mediaItems": [{"type": "video", "url": video_url}],
            "platforms": [{"platform": "tiktok", "accountId": account_id}],
            "tiktokSettings": settings,
            "publishNow": True,
        }
        r = requests.post(f"{ZERNIO_API_BASE}/posts", headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        result = r.json()
    except requests.exceptions.HTTPError as e:
        debug_dir = Path(config["output_dir"])
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_path = debug_dir / f"zernio_error_{source_text_path.stem}.txt"
        body = e.response.text if e.response is not None else ""
        debug_path.write_text(f"status_code: {e.response.status_code if e.response is not None else 'N/A'}\n"
                              f"url: {e.request.url if e.request is not None else ''}\nresponse_body: {body}\n",
                              encoding="utf-8")
        print("Zernio APIエラー。詳細:", debug_path)
        print(body)
        raise

    print("Zernio投稿結果:", json.dumps(result, ensure_ascii=False)[:600])
    post = result.get("post") or result
    record = {
        "posted_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds"),
        "source": str(source_text_path),
        "title": title,
        "voice_engine": voice.get("engine"),
        "voice": voice.get("voice"),
        "voice_reason": voice.get("reason"),
        "elevenlabs_remaining": voice.get("elevenlabs_remaining"),
        "music_id": music_id,
        "music": music_name,
        "zernio_post_id": post.get("_id") or post.get("id"),
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
