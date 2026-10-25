#!/usr/bin/env python3
"""
感動ストーリー(emotional_story_v3 の投稿文)から、ナレーション付きの縦型動画を作る(見本・2026-10-22)。

使い方:
    本番(GitHub Actions): python make_story_video.py --config accounts/<アカウント>.yaml <投稿文.txt> <出力フォルダ>
        アカウント設定の video: に テーマ・声・速さ を書く。ElevenLabs で作り、残りの文字数が足りない・
        エラーのときは VOICEVOX(無料)で作る。どちらで作ったかは 出力フォルダ/voice.json に残す
    手元の試し: python make_story_video.py <投稿文.txt> <出力フォルダ> [--voice <ElevenLabs voice_id>] [--tempo 1.0]

- 声: ElevenLabs(eleven_v3)。1文ずつ読ませ、文字起こし(scribe_v1)で読み間違いを確かめる(低ければ最大3回作り直す)
- 画面: 夕暮れ色のゆっくり動く背景＋光の粒。題名は上、本文は読んでいる文が浮かび上がり、読み終えた文は薄く残る
         「=====」の区切りでページが変わる
- 出力: 完成.mp4(1080x1920・30fps)・サムネ.png・読み上げ確認.txt
APIキー: 環境変数 ELEVENLABS_API_KEY、無ければ %USERPROFILE%\\.secrets\\elevenlabs_rootbuzz.txt
"""
import argparse
import difflib
import hashlib
import math
import os
import random
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS, SR = 1080, 1920, 30, 48000
API = "https://api.elevenlabs.io/v1"
MODEL = "eleven_v3"
DEFAULT_VOICE = "4lOQ7A2l7HPuG7UIHiKA"  # Kyoko - Warm, Clear and Natural(共有ライブラリ)

FONT_CANDIDATES = [
    os.environ.get("FONT_SERIF", ""),
    "fonts/NotoSerifJP-SemiBold.otf",
    "C:/Windows/Fonts/NotoSerifJP-VF.ttf",
    "C:/Windows/Fonts/yumin.ttf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
]
GAP_LINE = 0.45   # 文と文の間(秒)
GAP_PAGE = 1.0    # ページの切り替え
HEAD = 0.6        # 最初の無音
TAIL = 2.5        # 最後の余韻

# 読み間違えやすい語 → 読ませ方(画面の文はそのまま)
READ = [
    ("割烹着", "かっぽうぎ"), ("木綿", "もめん"), ("褪せて", "あせて"), ("下宿", "げしゅく"),
    ("襟", "えり"), ("受話器", "じゅわき"), ("袖", "そで"), ("温もり", "ぬくもり"),
    ("五年目", "ごねんめ"), ("一人きり", "ひとりきり"), ("鞄", "かばん"),
    ("ベン・フォールズ・ファイヴ13年", "ベンフォールズファイブ、13年"), ("ベン・フォールズ・ファイヴ", "ベンフォールズファイブ"),
    ("30th", "サーティース"), ("義母", "ぎぼ"), ("ファイヴ", "ファイブ"), ("13年ぶり", "じゅうさんねんぶり"),
    ("北米", "ほくべい"),
    ("わんちゃん", "ワンチャン"),   # 10-24 ひらがなだと「犬のわんちゃん」の発音になる(ユーザー指摘)
]


def find_font():
    for p in FONT_CANDIDATES:
        if p and Path(p).exists():
            return p
    raise SystemExit("明朝体のフォントが見つかりません")


def api_key():
    k = os.environ.get("ELEVENLABS_API_KEY")
    if k:
        return k.strip()
    return (Path(os.environ["USERPROFILE"]) / ".secrets" / "elevenlabs_rootbuzz.txt").read_text().strip()


# ---------- 投稿文を読む ----------

EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")


def split_sentences(line):
    """1行に複数の文があれば「。！？」で分ける(「」の中では分けない)"""
    out, cur, depth = [], "", 0
    for ch in line:
        cur += ch
        if ch in "「『":
            depth += 1
        elif ch in "」』":
            depth = max(0, depth - 1)
        elif ch in "。！？" and depth == 0:
            out.append(cur.strip())
            cur = ""
    if cur.strip():
        out.append(cur.strip())
    return out


def parse_story(path):
    """(題名, [ページ=[文,...], ...]) を返す。ハッシュタグ・最後の題名・絵文字は除く"""
    text = Path(path).read_text(encoding="utf-8")
    lines = [l.rstrip() for l in text.splitlines()]
    title = lines[0].lstrip("■").strip()
    body = []
    for l in lines[1:]:
        if l.startswith("#") or l.startswith("■"):
            continue
        body.append(l)
    pages, cur = [], []
    for l in body:
        if l.strip() == "=====":
            if cur:
                pages.append(cur)
            cur = []
            continue
        s = EMOJI.sub("", l).strip()
        if s:
            cur.extend(split_sentences(s))
    if cur:
        pages.append(cur)
    return title, pages


# ---------- 声 ----------

class ElevenLabsError(RuntimeError):
    pass


def elevenlabs_remaining():
    """ElevenLabs の今月の残り文字数(取れなければ None)"""
    try:
        r = requests.get(f"{API}/user/subscription", headers={"xi-api-key": api_key()}, timeout=30)
        r.raise_for_status()
        d = r.json()
        return d["character_limit"] - d["character_count"], d["character_limit"]
    except Exception as e:
        print(f"  ElevenLabs の残りを確かめられませんでした: {e}")
        return None


def to_reading(text):
    for a, b in READ:
        text = text.replace(a, b)
    return text


def _norm(s):
    s = "".join(ch for ch in s if ch not in "、。.,!?！？ 　\n「」『』…")
    for a, b in READ:
        s = s.replace(b, a)
    for k, n in zip("一二三四五六七八九", "123456789"):
        s = s.replace(k, n)
    return s


def _check(h, mp3, ref):
    with open(mp3, "rb") as f:
        r = requests.post(f"{API}/speech-to-text", headers=h,
                          data={"model_id": "scribe_v1", "language_code": "jpn"},
                          files={"file": f}, timeout=120)
    if not r.ok:
        return 1.0, "(確認できず)"
    heard = r.json().get("text", "")
    return difflib.SequenceMatcher(None, _norm(ref), _norm(heard)).ratio(), heard


def synth(text, voice, tempo, cache_dir, out_wav, log):
    h = {"xi-api-key": api_key()}
    say = to_reading(text)
    tag = hashlib.md5(f"{voice}|{MODEL}|{say}".encode()).hexdigest()[:12]
    best = cache_dir / f"{tag}.mp3"
    if not best.exists():
        tries = []
        for n in range(3):
            r = requests.post(f"{API}/text-to-speech/{voice}", headers=h,
                              json={"text": say, "model_id": MODEL, "language_code": "ja"}, timeout=180)
            if not r.ok:
                raise ElevenLabsError(f"ElevenLabs エラー {r.status_code}: {r.text[:300]}")
            p = cache_dir / f"{tag}_{n}.mp3"
            p.write_bytes(r.content)
            s, heard = _check(h, p, text)
            tries.append((s, p, heard))
            if s >= 0.95:
                break
        s, p, heard = max(tries, key=lambda x: x[0])
        p.replace(best)
        (cache_dir / f"{tag}.txt").write_text(f"{s:.3f}\t{heard}", encoding="utf-8")
    s, heard = (cache_dir / f"{tag}.txt").read_text(encoding="utf-8").split("\t", 1)
    log.append(f"[{s}] {text}\n      聞こえ方: {heard}")
    af = (f"atempo={tempo},"
          "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
          "silenceremove=start_periods=1:start_threshold=-45dB,areverse")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(best), "-af", af,
                    "-ac", "1", "-ar", str(SR), str(out_wav)], check=True)


VV = "http://127.0.0.1:50021"
VV_DOCKER_IMAGE = "voicevox/voicevox_engine:cpu-0.25.2"
VV_RUN = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages/HiroshibaKazuyuki.VOICEVOX_Microsoft.Winget.Source_8wekyb3d8bbwe/VOICEVOX/vv-engine/run.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/VOICEVOX/vv-engine/run.exe",
]


def ensure_voicevox():
    import time
    try:
        requests.get(f"{VV}/version", timeout=2)
        return
    except Exception:
        pass
    run = next((r for r in VV_RUN if r.exists()), None)
    if run:
        subprocess.Popen([str(run), "--host", "127.0.0.1", "--port", "50021"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif shutil.which("docker"):
        print("  VOICEVOX を Docker で起動します")
        subprocess.run(["docker", "run", "-d", "--rm", "-p", "127.0.0.1:50021:50021", VV_DOCKER_IMAGE],
                       check=True, stdout=subprocess.DEVNULL)
    else:
        raise SystemExit("VOICEVOX のエンジンが見つかりません")
    for _ in range(90):
        try:
            requests.get(f"{VV}/version", timeout=2)
            return
        except Exception:
            time.sleep(2)
    raise SystemExit("VOICEVOX のエンジンが起動しませんでした")


def synth_voicevox(text, speaker, tempo, out_wav, log):
    """VOICEVOX(無料)で読み上げる。読み方は audio_query のカナを記録して確かめる"""
    say = to_reading(text)
    q = requests.post(f"{VV}/audio_query", params={"text": say, "speaker": speaker}, timeout=60).json()
    q["speedScale"] = tempo
    q["prePhonemeLength"] = 0.05
    q["postPhonemeLength"] = 0.05
    log.append(f"{text}\n      読み: {q.get('kana', '')}")
    wav = requests.post(f"{VV}/synthesis", params={"speaker": speaker}, json=q, timeout=180).content
    tmp = Path(str(out_wav) + ".src.wav")
    tmp.write_bytes(wav)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-ac", "1", "-ar", str(SR), str(out_wav)], check=True)
    tmp.unlink()


GOOGLE_TTS = "https://texttospeech.googleapis.com/v1/text:synthesize"


class GoogleTTSError(RuntimeError):
    pass


def google_key():
    k = os.environ.get("GOOGLE_TTS_API_KEY")
    if k:
        return k.strip()
    f = Path(os.environ.get("USERPROFILE", "~")) / ".secrets" / "google_tts.txt"
    return f.read_text().strip() if f.exists() else ""


# ---- Google の無料枠(毎月100万字)を超えないための見張り(ユーザー「課金は絶対に避けたい」2026-10-23) ----
GOOGLE_FREE_CHARS = 1_000_000
GOOGLE_ALERT_RATIO = 0.30   # これを超えたら投稿のたびに TO 付きで警告(ふつうは月15%前後)
GOOGLE_STOP_RATIO = 0.60    # これを超えたらその月は Google を使わない(VOICEVOX にする)
GOOGLE_USED_THIS_RUN = [0]  # この実行で Google に送った文字数


def jst_month():
    import datetime
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).strftime("%Y-%m")


def google_usage_this_month():
    """全アカウントの outputs/*/google_tts_usage.jsonl を足した、今月(日本時間)の使用文字数"""
    import json
    month, total = jst_month(), 0
    for f in Path("outputs").glob("*/google_tts_usage.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("month") == month:
                total += int(rec.get("chars", 0))
    return total


def record_google_usage(output_dir, chars, title):
    import json
    import datetime
    if not chars:
        return
    f = Path(output_dir) / "google_tts_usage.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    rec = {"month": jst_month(), "chars": chars, "title": title,
           "at": (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).isoformat(timespec="seconds")}
    with open(f, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")


def chatwork_alert(body):
    """TO 付きで ChatWork に知らせる(トークンが無い手元の試しでは表示だけ)"""
    token, room = os.environ.get("CHATWORK_API_TOKEN"), os.environ.get("CHATWORK_ROOM_ID")
    print("  [警告] " + body.replace("\n", " "))
    if not token or not room:
        return
    try:
        requests.post(f"https://api.chatwork.com/v2/rooms/{room}/messages", headers={"X-ChatWorkToken": token},
                      data={"body": f"[To:237628]\n{body}"}, timeout=15)
    except Exception as e:
        print(f"  ChatWork に送れませんでした: {e}")


def synth_google(text, voice, rate, out_wav, log):
    """Google Cloud Text-to-Speech(Chirp 3 HD)で読み上げる。voice は「Kore」のような声の名前。
    速さは Google 側の speakingRate で指定する(後から伸び縮みさせないので音がきれい)"""
    import base64
    say = to_reading(text)
    GOOGLE_USED_THIS_RUN[0] += len(say)
    body = {
        "input": {"text": say},
        "voice": {"languageCode": "ja-JP", "name": f"ja-JP-Chirp3-HD-{voice}"},
        "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": SR, "speakingRate": rate},
    }
    r = requests.post(GOOGLE_TTS, params={"key": google_key()}, json=body, timeout=120)
    if not r.ok:
        raise GoogleTTSError(f"Google の読み上げエラー {r.status_code}: {r.text[:300]}")
    tmp = Path(str(out_wav) + ".src.wav")
    tmp.write_bytes(base64.b64decode(r.json()["audioContent"]))
    af = ("silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
          "silenceremove=start_periods=1:start_threshold=-45dB,areverse")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-af", af, "-ac", "1", "-ar", str(SR), str(out_wav)],
                   check=True)
    tmp.unlink()
    log.append(f"{text}\n      読ませ方: {say}")


def say_line(engine, text, voice, tempo, cache, out_wav, log):
    if engine == "google":
        synth_google(text, voice, tempo, out_wav, log)
    elif engine == "voicevox":
        synth_voicevox(text, int(voice), tempo, out_wav, log)
    else:
        synth(text, voice, tempo, cache, out_wav, log)


def read_wav(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


# ---------- 絵 ----------

def _greedy(text, font, max_w, draw):
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > max_w and cur:
            if ch in "、。」』）！？" and len(cur) > 1:
                lines.append(cur[:-1])
                cur = cur[-1] + ch
            else:
                lines.append(cur)
                cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


_WRAP_CACHE = {}


def wrap(text, font, max_w, draw):
    key = (text, id(font), max_w)
    if key not in _WRAP_CACHE:
        _WRAP_CACHE[key] = _wrap(text, font, max_w, draw)
    return _WRAP_CACHE[key]


def _kind(ch):
    if "ぁ" <= ch <= "ゟ":
        return "h"      # ひらがな
    if "゠" <= ch <= "ヿ":
        return "k"      # カタカナ
    if "一" <= ch <= "鿿" or ch in "々〆":
        return "j"      # 漢字
    return "o"


def _break_cost(text, c):
    """text[:c] と text[c:] の間で改行するときの不自然さ(小さいほど自然)。None は禁止"""
    prev, nxt = text[c - 1], text[c]
    if nxt in "、。」』）！？ーぁぃぅぇぉっゃゅょァィゥェォッャュョ" or prev in "「『（":
        return None
    if prev in "、。」』！？":
        return 0
    if nxt in "「『（":
        return 1
    if _kind(prev) == "h" and _kind(nxt) in "jk":
        return 2        # 「だけ|薄く」「と|笑い」のような、ひらがな→漢字の境目
    if _kind(prev) in "jk" and _kind(nxt) == "h":
        return 9        # 「笑|い」のような送り仮名の前は避ける
    return 8


def _wrap(text, font, max_w, draw):
    """1文を、日本語として自然な所で折り返す(「、」の後・ひらがな→漢字の境目・かっこの前)。
    行の長さもなるべくそろえる。行数は最少にする"""
    n = len(text)
    widths = [0.0] * (n + 1)
    for i in range(1, n + 1):
        widths[i] = draw.textlength(text[:i], font=font)
    if widths[n] <= max_w:
        return [text]
    min_lines = len(_greedy(text, font, max_w, draw))

    def solve(lines_n):
        """ちょうど lines_n 行に分けるときの (コスト, 行のリスト)"""
        INF = float("inf")
        target = widths[n] / lines_n
        best = [[INF] * (n + 1) for _ in range(lines_n + 1)]
        back = [[0] * (n + 1) for _ in range(lines_n + 1)]
        best[0][0] = 0
        for k in range(1, lines_n + 1):
            for i in range(1, n + 1):
                bc = 0 if i == n else _break_cost(text, i)
                if bc is None:
                    continue
                for j in range(i - 1, -1, -1):
                    w = widths[i] - widths[j]
                    if w > max_w:
                        break
                    if best[k - 1][j] == INF:
                        continue
                    cost = best[k - 1][j] + bc * 60 + abs(w - target) * 0.2
                    if cost < best[k][i]:
                        best[k][i] = cost
                        back[k][i] = j
        if best[lines_n][n] == INF:
            return INF, None
        out, i = [], n
        for k in range(lines_n, 0, -1):
            j = back[k][i]
            out.append(text[j:i])
            i = j
        return best[lines_n][n], out[::-1]

    # 1行増やすのは、切る所がよほど不自然なときだけ(1行あたり 150 の重み)
    options = []
    for extra in (0, 1):
        cost, lines = solve(min_lines + extra)
        if lines:
            options.append((cost + extra * 150, lines))
    if not options:
        return _greedy(text, font, max_w, draw)
    return min(options, key=lambda o: o[0])[1]


THEMES = {
    # 感動ストーリー: 夕暮れ色・明朝体・光の粒
    "story": dict(font="serif", grad=[(22, 24, 48), (70, 48, 72), (150, 92, 60)], particle=(255, 214, 160),
                  title=(255, 236, 205), rule=(255, 220, 170), text=(255, 250, 240), read_alpha=0.45,
                  shadow=True, tag=None, last_color=None),
    # ニュース: 紺〜青緑・ゴシック体・「NEWS」の札・最後の問いかけを黄色に
    "news": dict(font="sans", grad=[(10, 16, 34), (16, 40, 70), (18, 74, 96)], particle=(140, 210, 255),
                 title=(255, 255, 255), rule=(255, 80, 90), text=(245, 250, 255), read_alpha=0.42,
                 shadow=True, tag=("NEWS", (230, 40, 60), (255, 255, 255)), last_color=(255, 214, 90)),
    # 共感/議論系: 明るい桜色・ゴシック体・「みんなの意見募集」の札・最後の問いかけを強調
    "talk": dict(font="sans", grad=[(255, 246, 240), (252, 232, 236), (246, 214, 226)], particle=(255, 160, 190),
                 title=(70, 40, 60), rule=(235, 90, 130), text=(60, 46, 52), read_alpha=0.38,
                 shadow=False, tag=("みんなの意見募集", (235, 90, 130), (255, 255, 255)), last_color=(225, 50, 110),
                 big_tag=True, intro="意見ください！"),
}
# 効果音: 場面ごとに (ファイル名, 音量dB)。None は鳴らさない
# CapCut の効果音(依頼「ルートバズ」で取り込んだもの)。元の名前は Dropbox の 効果音/出典.txt
SFX = {
    "story": dict(start=("glocken.mp3", -16), page=("sparkle.mp3", -22), line=None, last=None),
    "news": dict(start=("impact.mp3", -10), page=("swipe.mp3", -12), line=("pop.mp3", -18), last=("sparkle.mp3", -14)),
    "talk": dict(start=("tsuzumi.mp3", -8), page=("swipe.mp3", -12), line=("pop.mp3", -15),   # 10-25 ぽよっを気持ち大きく(-18→-15)
              last=("notify.mp3", -12)),
}
SFX_DIRS = [
    Path(os.environ.get("SFX_DIR", "") or "."),
    Path(r"D:/Dropbox/株式会社アリウープ Dropbox/tsubata shingo/【クラウド使用】ソフトウェアデータフォルダ/Claude/AIを使った自動収益化の仕組み/効果音"),
]


def load_sfx(name, gain_db, work):
    """効果音を 48kHz モノラルで読み込む(見つからなければ None)。
    公開リポジトリには置けないので、GitHub では SFX_BASE_URL(非公開の場所)から読み込む"""
    src = next((d / name for d in SFX_DIRS if (d / name).exists()), None)
    base = os.environ.get("SFX_BASE_URL", "").strip()
    # 10-24〜 本命: リポジトリの暗号化した効果音(assets/sfx.tar.enc)を、Secrets の SFX_BASE_URL を合言葉にして開く
    # (GitHub から Xserver への取得が時間切れになることが続いたため。暗号化しているので公開にはならない)
    enc = Path("assets/sfx.tar.enc")
    unpacked = work / "sfx_src"
    if not src and base and enc.exists():
        if not (unpacked / name).exists():
            unpacked.mkdir(parents=True, exist_ok=True)
            try:
                env = dict(os.environ, SFX_PASS=base)
                dec = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-in", str(enc), "-pass", "env:SFX_PASS"],
                                     capture_output=True, env=env, check=True)
                subprocess.run(["tar", "-xf", "-", "-C", str(unpacked)], input=dec.stdout, check=True)
            except Exception as e:
                print(f"  暗号化した効果音を開けませんでした: {e}")
        if (unpacked / name).exists():
            src = unpacked / name
    if not src and base:
        dl = work / "sfx_src" / name
        dl.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):   # 10-23 GitHub から Xserver につながらず時間切れ → やり直し、だめなら効果音なし
            if dl.exists():
                break
            try:
                r = requests.get(base.rstrip("/") + "/" + name, timeout=20)
                if r.ok:
                    dl.write_bytes(r.content)
            except Exception as e:
                print(f"  効果音を取れませんでした({attempt + 1}回目): {name} {e}")
        if dl.exists():
            src = dl
    if not src:
        print(f"  効果音が見つかりません: {name}")
        return None
    out = work / ("sfx_" + hashlib.md5(name.encode()).hexdigest()[:8] + ".wav")
    if not out.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", str(SR), str(out)], check=True)
    return read_wav(out) * (10 ** (gain_db / 20))


SANS_CANDIDATES = [
    os.environ.get("FONT_SANS", ""),
    "fonts/NotoSansJP-Bold.otf",
    "C:/Windows/Fonts/NotoSansJP-VF.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]


def find_sans():
    for p in SANS_CANDIDATES:
        if p and Path(p).exists():
            return p
    return find_font()


class Painter:
    def __init__(self, theme, seed, credit=None):
        self.th = THEMES[theme]
        self.credit = credit
        font_path = find_font() if self.th["font"] == "serif" else find_sans()
        self.title_font = ImageFont.truetype(font_path, 74)
        self.body_font = ImageFont.truetype(font_path, 52)
        self.tag_font = ImageFont.truetype(find_sans(), 40)
        self.big_tag_font = ImageFont.truetype(find_sans(), 78)
        self.credit_font = ImageFont.truetype(find_sans(), 30)
        try:
            self.title_font.set_variation_by_axes([800 if self.th["font"] == "sans" else 700])
            self.body_font.set_variation_by_axes([600 if self.th["font"] == "sans" else 500])
            self.tag_font.set_variation_by_axes([800])
            self.big_tag_font.set_variation_by_axes([900])
        except Exception:
            pass
        rnd = random.Random(seed)
        self.particles = [(rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(6, 26),
                           rnd.uniform(8, 30), rnd.uniform(0, math.tau), rnd.uniform(0.15, 0.5))
                          for _ in range(38)]
        yy = np.linspace(0, 1, H)[:, None]
        self.yy = np.repeat(yy, W, axis=1)
        xx = np.linspace(-1, 1, W)[None, :]
        ry = np.linspace(-1, 1, H)[:, None]
        dark = 0.55 if self.th["shadow"] else 0.12
        self.vignette = np.clip(1 - dark * (xx ** 2 * 0.7 + ry ** 2 * 0.5), 0, 1)[..., None]

    def _gradient(self):
        top, mid, bot = (np.array(c) for c in self.th["grad"])
        y = self.yy[..., None]
        img = np.where(y < 0.55, top + (mid - top) * (y / 0.55), mid + (bot - mid) * ((y - 0.55) / 0.45))
        img = img * self.vignette
        return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).convert("RGBA")

    def background(self, t):
        if not hasattr(self, "_grad"):
            self._grad = self._gradient()
        base = self._grad.copy()
        # 光の粒は縮小して描いてからぼかして拡大する(速さのため)
        glow = Image.new("RGBA", (W // 3, H // 3), (0, 0, 0, 0))
        d = ImageDraw.Draw(glow)
        pc = self.th["particle"]
        for x, y0, r, speed, ph, a in self.particles:
            y = (y0 - speed * t) % (H + 80) - 40
            xx = x + 18 * math.sin(t * 0.4 + ph)
            alpha = int(255 * a * (0.6 + 0.4 * math.sin(t * 0.8 + ph)))
            d.ellipse([(xx - r) / 3, (y - r) / 3, (xx + r) / 3, (y + r) / 3], fill=pc + (alpha,))
        glow = glow.filter(ImageFilter.GaussianBlur(3)).resize((W, H), Image.BILINEAR)
        base.alpha_composite(glow)
        return base

    def _text(self, d, xy, text, font, color, alpha):
        if self.th["shadow"]:
            d.text((xy[0] + 2, xy[1] + 3), text, font=font, fill=(0, 0, 0, int(alpha * 0.5)))
        d.text(xy, text, font=font, fill=tuple(color) + (int(alpha),))

    def text_block(self, title, page_lines, active, appear, fade_page=1.0, is_last_page=False, t=0.0):
        """題名と、ページの文(読んだ所まで)を描いた透明レイヤー"""
        th = self.th
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        y = 200
        if self.credit:
            cw = d.textlength(self.credit, font=self.credit_font)
            col = (255, 255, 255, 150) if th["shadow"] else (90, 70, 80, 150)
            d.text((W - cw - 40, H - 90), self.credit, font=self.credit_font, fill=col)
        # 札(NEWS など)
        if th.get("big_tag"):
            # 大きな帯の札。ゆっくり脈打たせて目を引く
            label, bg, fg = th["tag"]
            pulse = 1 + 0.035 * math.sin(t * 5)
            f = self.big_tag_font
            tw = d.textlength(label, font=f)
            bw, bh = (tw + 110) * pulse, 130 * pulse
            x0, y0 = (W - bw) / 2, 150 + (130 - bh) / 2
            d.rounded_rectangle([x0 + 6, y0 + 10, x0 + bw + 6, y0 + bh + 10], radius=34, fill=(150, 40, 80, 90))
            d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=34, fill=bg + (255,))
            d.text(((W - tw) / 2, y0 + (bh - 100) / 2), label, font=f, fill=fg + (255,))
            y = 330
        elif th["tag"]:
            label, bg, fg = th["tag"]
            tw = d.textlength(label, font=self.tag_font)
            x0 = (W - tw) / 2 - 30
            d.rounded_rectangle([x0, y, x0 + tw + 60, y + 70], radius=35, fill=bg + (255,))
            d.text((x0 + 30, y + 10), label, font=self.tag_font, fill=fg + (255,))
            y += 110
        else:
            y += 30
        # 題名
        for l in wrap(title, self.title_font, W - 160, d):
            w = d.textlength(l, font=self.title_font)
            self._text(d, ((W - w) / 2, y), l, self.title_font, th["title"], 255)
            y += 98
        d.line([(W / 2 - 120, y + 12), (W / 2 + 120, y + 12)], fill=th["rule"] + (200,), width=4)
        top = y + 90

        # 本文(読んでいる文が下にはみ出すときは、全体を上にずらす)
        blocks = [wrap(s, self.body_font, W - 190, d) for s in page_lines]
        line_h, para_gap = 80, 30
        heights = [len(b) * line_h for b in blocks]
        total = sum(heights) + para_gap * (len(blocks) - 1)
        y = max(top, (H - total) / 2 + 40)
        if active >= 0:
            bottom = y + sum(heights[:active + 1]) + para_gap * active
            limit = H - 220
            if bottom > limit:
                y -= bottom - limit
        for i, b in enumerate(blocks):
            if i > active:
                break
            if i < active:
                alpha, dy = th["read_alpha"], 0
            else:
                e = 1 - (1 - min(1, appear)) ** 3
                alpha, dy = e, (1 - e) * 26
            color = th["text"]
            if is_last_page and i == len(blocks) - 1 and th["last_color"]:
                color = th["last_color"]
            for l in b:
                if y + dy > top - 40:   # 題名の下より上には描かない
                    self._text(d, (95, y + dy), l, self.body_font, color, 255 * alpha * fade_page)
                y += line_h
            y += para_gap
        return layer


def build(story_txt, out_dir, voice, tempo, theme="story", engine="elevenlabs", credit=None, sfx=True):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(os.environ.get("LOCALAPPDATA", "/tmp")) / "story_video_work"
    cache = work / "voice_cache"
    cache.mkdir(parents=True, exist_ok=True)

    title, pages = parse_story(story_txt)
    log = []

    # 1) 声: 題名 → 各文。並べて1本の音声にし、各文の開始時刻を記録する
    if engine == "voicevox":
        ensure_voicevox()
    pieces = []  # (page, index, start, end)
    audio = [np.zeros(int(SR * HEAD), np.float32)]
    t = HEAD
    intro = THEMES[theme].get("intro")
    if intro:
        # 冒頭のひとこと(例「意見ください！」)を早口で
        iw = work / "intro.wav"
        say_line(engine, intro, voice, tempo * 1.3, cache, iw, log)
        a = read_wav(iw)
        audio.append(a)
        t += len(a) / SR
        audio.append(np.zeros(int(SR * 0.15), np.float32))
        t += 0.15
    tw = work / "title.wav"
    say_line(engine, title, voice, tempo, cache, tw, log)
    a = read_wav(tw)
    title_span = (t, t + len(a) / SR)
    audio.append(a)
    t += len(a) / SR
    for pi, page in enumerate(pages):
        gap = GAP_PAGE if pi else 0.9
        audio.append(np.zeros(int(SR * gap), np.float32))
        t += gap
        for si, s in enumerate(page):
            if si:
                audio.append(np.zeros(int(SR * GAP_LINE), np.float32))
                t += GAP_LINE
            p = work / f"p{pi}_{si}.wav"
            say_line(engine, s, voice, tempo, cache, p, log)
            a = read_wav(p)
            pieces.append((pi, si, t, t + len(a) / SR))
            audio.append(a)
            t += len(a) / SR
    audio.append(np.zeros(int(SR * TAIL), np.float32))
    total = t + TAIL
    voice_all = np.concatenate(audio)

    # 効果音: 最初 → 文が出るたび → ページが変わるとき → 最後の問いかけ
    if sfx:
        plan = SFX[theme]
        events = [(0.0, plan["start"])]
        for k, (pi, si, st, _) in enumerate(pieces):
            if pi == len(pages) - 1 and si == len(pages[pi]) - 1 and plan["last"]:
                events.append((st - 0.15, plan["last"]))
            elif si == 0 and pi > 0 and plan["page"]:
                events.append((st - 0.35, plan["page"]))
            elif plan["line"]:
                events.append((st - 0.15, plan["line"]))
        for at, item in events:
            if not item:
                continue
            clip = load_sfx(item[0], item[1], work)
            if clip is None:
                continue
            i0 = max(0, int(at * SR))
            i1 = min(len(voice_all), i0 + len(clip))
            voice_all[i0:i1] += clip[:i1 - i0]
    narr = work / "narration.wav"
    with wave.open(str(narr), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(voice_all, -1, 1) * 32767).astype(np.int16).tobytes())
    (out_dir / "読み上げ確認.txt").write_text(
        f"声: {engine} {voice} / 速さ {tempo}\n長さ: {total:.1f}秒\n\n" + "\n".join(log), encoding="utf-8")

    # 2) 絵: フレームを ffmpeg に流し込む
    painter = Painter(theme, seed=hash(title) & 0xffff, credit=credit)
    n_frames = int(total * FPS)
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "-", "-i", str(narr), "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-shortest", str(work / "完成.mp4")],
        stdin=subprocess.PIPE)
    thumb_done = False
    for f in range(n_frames):
        tt = f / FPS
        frame = painter.background(tt)
        # 今どのページ・どの文か
        cur = None
        for pc in pieces:
            if tt >= pc[2] - 0.15:
                cur = pc
        if cur is None:
            # 題名だけ。最初のコマから出しておく(冒頭1秒で離脱されないように)
            layer = painter.text_block(title, [], -1, 0, t=tt)
        else:
            pi, si, st, _ = cur
            appear = (tt - (st - 0.15)) / 0.55
            fade_page = 1.0
            # ページの最後の文を読み終えたら、次のページの前に少し薄くする
            nxt = [pc for pc in pieces if pc[0] == pi + 1]
            if nxt and si == len(pages[pi]) - 1:
                fade_page = max(0.0, min(1.0, (nxt[0][2] - 0.15 - tt) / 0.5))
            layer = painter.text_block(title, pages[pi], si, appear, fade_page, is_last_page=(pi == len(pages) - 1), t=tt)
        frame.alpha_composite(layer)
        # 最後に全体を暗くして終える
        if tt > total - 1.2:
            k = (tt - (total - 1.2)) / 1.2
            frame = Image.blend(frame, Image.new("RGBA", (W, H), (0, 0, 0, 255)), min(1, k) * 0.85)
        rgb = frame.convert("RGB")
        if not thumb_done and cur and cur[0] == 0 and cur[1] == len(pages[0]) - 1 and tt > cur[2] + 0.8:
            rgb.save(out_dir / "サムネ.png")
            thumb_done = True
        proc.stdin.write(rgb.tobytes())
        if f % (FPS * 10) == 0:
            print(f"  {tt:.0f}/{total:.0f}秒", flush=True)
    proc.stdin.close()
    proc.wait()
    # 作業場所で作ってから置き換える(完成.mp4 を開いていると書き込めないため)
    dest = out_dir / "完成.mp4"
    try:
        shutil.copyfile(work / "完成.mp4", dest)
    except PermissionError:
        dest = out_dir / "完成_新.mp4"
        shutil.copyfile(work / "完成.mp4", dest)
        print("  完成.mp4 が開かれていて置き換えられないため、完成_新.mp4 に保存しました")
    print(f"完成: {dest}（{total:.1f}秒）")


def build_for_account(config_path, story_txt, out_dir):
    """アカウント設定の video: に従って作る。声は ElevenLabs → Google(Chirp 3 HD)→ VOICEVOX の順に(ユーザー希望)、
    使えるものを使う。どれで作ったかを voice.json に残す"""
    import json
    import yaml
    acc = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    cfg = acc["video"]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if (out_dir / "完成.mp4").exists() and (out_dir / "voice.json").exists():
        print("動画はできています(見本で確認済み)。作り直しません")
        return json.loads((out_dir / "voice.json").read_text(encoding="utf-8"))
    title, pages = parse_story(story_txt)
    chars = sum(len(x) for pg in pages for x in pg) + 40
    reasons = []
    engine = None

    # 1) ElevenLabs(キーがあって、今月の残りが足りるとき)
    if cfg.get("elevenlabs_voice") and os.environ.get("ELEVENLABS_API_KEY"):
        rem = elevenlabs_remaining()
        need = int(chars * 1.6) + 200   # 読み直し(最大3回)の分も少し見込む
        if rem is not None and rem[0] < need:
            reasons.append(f"ElevenLabs の残りが足りない(残り{rem[0]}字・必要{need}字)")
        else:
            try:
                build(story_txt, out_dir, cfg["elevenlabs_voice"], cfg.get("elevenlabs_tempo", 1.0), cfg["theme"])
                engine = "elevenlabs"
            except ElevenLabsError as e:
                reasons.append(f"ElevenLabs のエラー: {e}")

    # 2) Google(Chirp 3 HD): 無料枠の見張りつき
    used = None
    if engine is None and cfg.get("google_voice") and google_key():
        used = google_usage_this_month()
        ratio = (used + chars) / GOOGLE_FREE_CHARS
        if ratio >= GOOGLE_STOP_RATIO:
            reasons.append(f"Google の今月の使用が無料枠の{GOOGLE_STOP_RATIO:.0%}を超えたので止めた")
            chatwork_alert(f"[info][title]⚠️Google 読み上げを自動で止めました[/title]今月の使用：{used:,}字／無料枠 {GOOGLE_FREE_CHARS:,}字（{used / GOOGLE_FREE_CHARS:.0%}）\n"
                           f"課金を避けるため、今月の残りは VOICEVOX（無料）で作ります。ふつうは月15%前後なので、何かがおかしい可能性があります。[/info]")
        else:
            if used / GOOGLE_FREE_CHARS >= GOOGLE_ALERT_RATIO:
                chatwork_alert(f"[info][title]⚠️Google 読み上げの使用が多めです[/title]今月の使用：{used:,}字／無料枠 {GOOGLE_FREE_CHARS:,}字（{used / GOOGLE_FREE_CHARS:.0%}）\n"
                               f"{GOOGLE_STOP_RATIO:.0%}に達すると自動で止めて VOICEVOX にします。ふつうは月15%前後です。[/info]")
            try:
                build(story_txt, out_dir, cfg["google_voice"], cfg.get("google_rate", 1.0), cfg["theme"], engine="google")
                engine = "google"
            except GoogleTTSError as e:
                reasons.append(f"Google のエラー: {e}")
                print(f"  {reasons[-1]} → 次の声で作り直します")
            finally:
                record_google_usage(acc["output_dir"], GOOGLE_USED_THIS_RUN[0], title)
    elif engine is None and cfg.get("google_voice"):
        reasons.append("Google のキーが無い")

    # 3) VOICEVOX(無料・いつでも使える)
    if engine is None:
        build(story_txt, out_dir, str(cfg["voicevox_speaker"]), cfg.get("voicevox_tempo", 1.0), cfg["theme"],
              engine="voicevox", credit=f"VOICEVOX:{cfg['voicevox_name']}")
        engine = "voicevox"

    names = {"google": "Google", "elevenlabs": "ElevenLabs", "voicevox": "VOICEVOX"}
    voice = {"google": cfg.get("google_voice"), "elevenlabs": cfg.get("elevenlabs_voice"),
             "voicevox": f"{cfg['voicevox_name']}({cfg['voicevox_speaker']})"}[engine]
    rem = elevenlabs_remaining() if os.environ.get("ELEVENLABS_API_KEY") else None
    info = {"engine": names[engine], "voice": voice, "reason": " / ".join(reasons), "chars": chars,
            "google_used_this_month": (used or 0) + GOOGLE_USED_THIS_RUN[0] if used is not None else None,
            "google_free_chars": GOOGLE_FREE_CHARS,
            "elevenlabs_remaining": rem[0] if rem else None, "elevenlabs_limit": rem[1] if rem else None}
    (out_dir / "voice.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"声: {info['engine']} {info['voice']} {info['reason']}")
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="本番用: アカウント設定(video: の設定で作る)")
    ap.add_argument("story_txt")
    ap.add_argument("out_dir")
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--tempo", type=float, default=1.0)
    ap.add_argument("--theme", default="story", choices=list(THEMES))
    ap.add_argument("--engine", default="elevenlabs", choices=["elevenlabs", "voicevox", "google"])
    ap.add_argument("--no-sfx", action="store_true", help="効果音を入れない")
    ap.add_argument("--credit", default=None, help="画面の右下に出す表記(VOICEVOX は「VOICEVOX:キャラ名」が必要)")
    a = ap.parse_args()
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg がありません")
    if a.config:
        build_for_account(a.config, a.story_txt, a.out_dir)
        return
    build(a.story_txt, a.out_dir, a.voice, a.tempo, a.theme, a.engine, a.credit, not a.no_sfx)


if __name__ == "__main__":
    main()
