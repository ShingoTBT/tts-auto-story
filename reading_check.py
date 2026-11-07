#!/usr/bin/env python3
"""
読み上げる前に、原稿の固有名詞・流行りの言葉の読み方を AI がネットで調べて確かめる(2026-11-07〜)。
きっかけ：ニュースで「小嶋陽菜（こじまはるな）」を「こじまひな」と読んだ(ユーザー「人物名を間違えるのは致命的」)。

- 対象：人名・グループ名・チーム名・地名・店名・作品名・商品名・流行りの言葉・辞書に載っていない言葉など全部
- 調べた結果は outputs/reading_dict.json にためる(同じ言葉は次から調べない。GitHub の投稿のあとに一緒に保存される)
- 読み方が分かった言葉は、声にだけひらがな・カタカナで渡す(画面の字幕は漢字のまま)
- 調べられなかったとき(AI のエラーなど)は、辞書にある分だけ使って先に進む(動画は止めない)

使い方(試し)：python reading_check.py 原稿.txt
"""
import json
import os
import re
import sys
from pathlib import Path

MODEL = "claude-opus-5-5"
DICT_PATH = Path("outputs/reading_dict.json")


def load_dict():
    try:
        return json.loads(DICT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_dict(d):
    DICT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DICT_PATH.write_text(json.dumps(dict(sorted(d.items())), ensure_ascii=False, indent=1), encoding="utf-8")


PROMPT = """これからナレーション（AI の声）で読み上げる日本語の原稿です。読み間違いを防ぐため、読み方を確かめてください。

原稿：
<<<
{text}
>>>

{known}手順：
1. 原稿から、次の言葉を「漏れなく全部」抜き出す。
   - 人の名前（芸能人・スポーツ選手・政治家など。フルネームでも、姓だけ・名前だけでも）
   - グループ名・チーム名・会社名・団体名・店名・番組名・作品名・商品名・イベント名
   - 地名・駅名・施設名
   - 流行りの言葉・ネットの言葉・略語・辞書に載っていないような言葉・読み方が何通りもある言葉・英字や記号の名前
2. 人の名前と、読み方に少しでも自信がないものは、必ずウェブ検索で正しい読み方を確かめる（公式サイト・本人のSNS・Wikipedia・ニュースのふりがな など）。
   推測で答えない。検索しても確かめられなければ confirmed を false にする。
3. 最後に、JSON だけを返す（説明の文は書かない）：
{{"words": [{{"word": "原稿に書いてある通りの文字", "reading": "読み方（ひらがな。外来語・英字の名前はカタカナ）", "kind": "人名/グループ名/チーム名/地名/言葉 など", "confirmed": true, "source": "確かめた所（サイト名など短く）", "replace": true}}]}}

replace：声に読み方の文字（ひらがな・カタカナ）を渡すかどうか。
- 人の名前・グループ名・チーム名・珍しい地名・流行りの言葉・英字の名前など、読み上げの機械が間違えそうなものは true
- 「日本」「東京」「アメリカ」のように誰でも読める言葉は false（不自然な発音になるのを避けるため）
"""


def _ask(text, known):
    import anthropic
    client = anthropic.Anthropic()
    known_txt = ""
    if known:
        known_txt = "次の言葉は読み方を確認済みなので、調べなくてよいです（JSON にも入れなくてよい）：\n" + \
            "、".join(f"{w}（{v['reading']}）" for w, v in known.items()) + "\n\n"
    messages = [{"role": "user", "content": PROMPT.format(text=text, known=known_txt)}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 15,
              "user_location": {"type": "approximate", "country": "JP"}}]
    for _ in range(5):
        r = client.beta.messages.create(
            model=MODEL, max_tokens=16000, messages=messages, tools=tools,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        if r.stop_reason == "pause_turn":
            messages = messages[:1] + [{"role": "assistant", "content": r.content}]
            continue
        break
    if r.stop_reason == "refusal":
        raise RuntimeError("AI が答えませんでした(refusal)")
    out = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise RuntimeError(f"答えの形が違います: {out[:200]}")
    searches = getattr(getattr(r.usage, "server_tool_use", None), "web_search_requests", None)
    return json.loads(m.group(0)).get("words", []), searches


_KANA = re.compile(r"^[ぁ-ゟ゠-ヿー・\s]+$")


def check(texts):
    """原稿の文の一覧を受け取り、声に渡す置き換え [(元の言葉, 読み方), ...] と、確認の記録(文字列の一覧)を返す"""
    text = "\n".join(texts)
    d = load_dict()
    known = {w: v for w, v in d.items() if w in text}
    notes = []
    try:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY がありません")
        words, searches = _ask(text, known)
        notes.append(f"（AI が読み方を確認。ウェブ検索 {searches} 回）")
        for w in words:
            word, reading = (w.get("word") or "").strip(), (w.get("reading") or "").strip()
            if not word or not reading or word not in text or not _KANA.match(reading):
                continue
            entry = {"reading": reading, "kind": w.get("kind", ""), "confirmed": bool(w.get("confirmed")),
                     "source": w.get("source", ""), "replace": bool(w.get("replace"))}
            known[word] = entry
            # 確かめられたものだけ辞書にためる(確かめられなかったものは次の回にまた調べる)
            if entry["confirmed"]:
                d[word] = entry
        save_dict(d)
    except Exception as e:
        notes.append(f"（読み方の確認ができませんでした。辞書にある言葉だけ使います：{type(e).__name__}: {e}）")
    pairs = []
    for word, v in sorted(known.items(), key=lambda x: -len(x[0])):
        mark = "" if v.get("confirmed") else "（未確認）"
        how = "→ 声はこの読みで" if v.get("replace") and word != v["reading"] else "（そのまま）"
        notes.append(f"{word}：{v['reading']}　{v.get('kind', '')}{mark}　{v.get('source', '')}　{how}")
        if v.get("replace") and word != v["reading"]:
            pairs.append((word, v["reading"]))
    return pairs, notes


if __name__ == "__main__":
    t = Path(sys.argv[1]).read_text(encoding="utf-8")
    pairs, notes = check([l for l in t.splitlines() if l.strip() and not l.startswith("#")])
    print("\n".join(notes))
