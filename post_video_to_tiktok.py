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
MUSIC_VOLUME = 11       # 曲の音量(0〜100)。ナレーションが主役なので小さく(依頼「ルートバズ」で 15 は大きいと言われた。10-24 8 では「もう少しだけ上げて良さそう」→ 11)
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


MUSIC_CACHE = Path("outputs/music_cache.json")
# 一覧が空のときの控え(10-22 に一覧から取れた曲と、実際に投稿で使えた曲。clip.id)。
# 10-23 朝から Zernio の商用曲の一覧がどのアカウントでも空で返り、全部の投稿が止まったため
MUSIC_FALLBACK = [
    # 実際に投稿で使えた曲
    {"id": "7317215175366690817", "name": "2:23 AM / しゃろう", "genres": ["HIP_HOP/RAP", "ALTERNATIVE_HIP_HOP"]},
    {"id": "7316669896233650177", "name": "怪しい屋敷を探索する的なBGM / 鷹尾まさき", "genres": ["POP"]},
    {"id": "7467109171273517073", "name": "おつかれさまのうた / kimimi", "genres": ["POP", "CHINESE_POP"]},
    {"id": "6873874436664084482", "name": "Forgiveness / Renee Michele", "genres": ["POP", "EASY_LISTENING"]},
    {"id": "7441962111373576209", "name": "Pray For The Lost / Worship Tree", "genres": ["NEW_AGE"]},
    {"id": "7210601088247711745", "name": "Land / Adrián Berenguer", "genres": ["NEW_AGE", "CLASSICAL"]},
    {"id": "6817195494956075009", "name": "Piano famous song Chopin / RYOpianoforte", "genres": ["CLASSICAL"]},
    {"id": "7488898663911442449", "name": "Little Love Song / kixx John", "genres": ["POP", "INDIE_POP"]},
    {"id": "7031666552407590913", "name": "Happy Light Jazz - Cafe3 / Kobat", "genres": ["JAZZ", "EASY_LISTENING"]},
    # 10-22 の一覧にあった曲
    {"id": "7665652448522274832", "name": "Tokimeki Records", "genres": ["POP"]},
    {"id": "7540525792741853201", "name": "Nana's room", "genres": ["POP", "INDIE_POP"]},
    {"id": "7478947357292824577", "name": "CopyA", "genres": ["POP"]},
    {"id": "7315927291699595266", "name": "shimtone", "genres": ["POP"]},
    {"id": "7314370276128704514", "name": "sanusagi", "genres": ["POP"]},
    {"id": "7423305583476475920", "name": "みずかげりんぶ", "genres": ["ELECTRONIC"]},
    {"id": "7637031538719606785", "name": "DJBENNY", "genres": ["POP", "DANCE_POP"]},
    {"id": "7464737604120283152", "name": "edivieira", "genres": ["ELECTRONIC", "TROPICAL_HOUSE"]},
    {"id": "6799249427404949505", "name": "Kaleidoscope / Adrián Berenguer", "genres": ["NEW_AGE"]},
    {"id": "7453840990354376721", "name": "Sad Waves", "genres": ["NEW_AGE"]},
    {"id": "7063427211096754177", "name": "Adrián Berenguer", "genres": ["CLASSICAL"]},
    {"id": "6817660390311200770", "name": "arachang", "genres": ["EASY_LISTENING"]},
    {"id": "6817359039727405058", "name": "SOUND BANK", "genres": ["JAZZ"]},
    {"id": "6849405904635299841", "name": "Matt Larsson", "genres": ["JAZZ"]},
]


def list_tracks(headers: dict, account_id: str) -> list:
    """商用曲の一覧(clip のあるものだけ)。空なら1回やり直し、それでも空なら控えを使う"""
    import time
    for attempt in range(2):
        try:
            r = requests.get(f"{ZERNIO_API_BASE}/accounts/{account_id}/tiktok/commercial-music",
                             params={"countryCode": "JP"}, headers=headers, timeout=60)
            r.raise_for_status()
            tracks = [{"id": t["clip"]["id"], "name": f"{t.get('name') or ''} / {t.get('artist') or ''}",
                       "genres": t.get("genres") or [], "sec": t["clip"].get("durationSec") or 0}
                      for t in r.json().get("tracks", []) if (t.get("clip") or {}).get("id")]
        except Exception as e:
            print(f"  商用曲の一覧を取れませんでした: {e}")
            tracks = []
        if tracks:
            try:   # 取れたときは控えを新しくしておく(次に空だったとき用)
                MUSIC_CACHE.parent.mkdir(parents=True, exist_ok=True)
                MUSIC_CACHE.write_text(json.dumps({"saved_at": datetime.date.today().isoformat(), "tracks": tracks},
                                                  ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass
            return tracks
        time.sleep(5)
    if MUSIC_CACHE.exists():
        try:
            cached = json.loads(MUSIC_CACHE.read_text(encoding="utf-8")).get("tracks") or []
            if cached:
                print(f"  商用曲の一覧が空 → 前に取れた一覧の控え({len(cached)}曲)を使います")
                return cached
        except Exception:
            pass
    print(f"  商用曲の一覧が空 → 決めてある控え({len(MUSIC_FALLBACK)}曲)を使います")
    return MUSIC_FALLBACK


def pick_music(headers: dict, account_id: str, genres: list, video_sec: float, avoid: set, exclude_words=()):
    """商用曲から、ジャンルが合う曲を選ぶ(曲名に exclude_words を含むものは除く)"""
    tracks = list_tracks(headers, account_id)
    ng = [w.lower() for w in exclude_words or []]
    tracks = [t for t in tracks if not any(w in (t.get("name") or "").lower() for w in ng)] or tracks
    want = set(genres or [])
    match = [t for t in tracks if want & set(t.get("genres") or [])] or tracks[:30]
    long_enough = [t for t in match if (t.get("sec") or 999) >= video_sec] or match
    fresh = [t for t in long_enough if t["id"] not in avoid] or long_enough
    t = random.choice(fresh[:20])
    return t["id"], t.get("name") or ""


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
