"""
TikTok の説明欄に入れる、短い「つかみ」の文(2026-10-26〜 ユーザー希望)

説明欄を「■タイトル / つかみ / #ハッシュタグ」にする。つかみは動画を最後まで観たくなるためのもので、
中身を全部は書かない(結末・オチ・答えは伏せる)。「〇〇な人は必見！」「衝撃、なんと〇〇が〇〇に！？」のようなキャッチーな一言。
作れなかったとき・AI が作ったとわかる文になったときは、つかみ無し(今までどおり)で投稿する。
"""

import os
import re

TEASER_MODEL = "claude-sonnet-4-6"
MAX_CHARS = 70

STYLE = {
    "news": "芸能・スポーツのニュース。驚きや意外性で引きつける（例：「衝撃、なんと〇〇が〇〇に！？」「〇〇ファンは必見！」）",
    "explain": "社会人向けの時事ニュースを、やさしく解説する動画。「知らないとまずいかも」「そういうことか」と思わせる一言（例：「最近よく聞く〇〇、実はこういうことなんです」「社会人なら押さえておきたい〇〇の話」）。煽りすぎない",
    "talk": "義実家・ママ友・夫婦などの、モヤモヤする体験談。共感と「この後どうなる？」で引きつける（例：「義実家に悩む人は必見…」「まさかの一言に絶句」）",
    "story": "家族の感動ストーリー。静かで温かい雰囲気を壊さないこと。煽る言葉・「衝撃」「必見」などは使わず、そっと続きが気になる一言に（例：「最後に見つけたものに、涙が止まりませんでした」）",
}


def body_of(source_text: str) -> str:
    """原稿から本文だけ(タイトル行・区切り・ハッシュタグ・末尾のタイトル再掲を除く)"""
    rows = [l.strip() for l in source_text.split("\n")]
    return "\n".join(l for l in rows[1:] if l and not l.startswith("#") and not l.startswith("■") and not re.fullmatch(r"=+", l))


def make_teaser(source_text: str, theme: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or theme not in STYLE:
        return ""
    title = source_text.split("\n", 1)[0].strip()
    prompt = (
        "TikTok の縦型動画の説明欄に、タイトルの下に入れる「つかみ」の文を書いてください。\n"
        f"動画の種類：{STYLE[theme]}\n"
        "決まり：\n"
        "- 動画を最後まで観たくなる一言〜二文。中身を全部は書かない。結末・オチ・答え・最後の一言は絶対に書かない（続きが気になる所で止める）\n"
        f"- 全部で{MAX_CHARS}文字以内。ハッシュタグ・URL は入れない。絵文字は入れても1つまで\n"
        "- 動画の内容に無いことは書かない。タイトルと同じ言い回しの繰り返しにしない\n"
        "- 本人が書いた投稿として自然な文だけを書く（前置き・説明・かっこ書き・「つかみ：」のような見出しは書かない）\n\n"
        f"【タイトル】\n{title}\n\n【動画のナレーション】\n{body_of(source_text)}\n\n"
        "つかみの文だけを返してください。"
    )
    try:
        import anthropic
        r = anthropic.Anthropic(api_key=key).messages.create(
            model=TEASER_MODEL, max_tokens=200, messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        print(f"  説明欄のつかみを作れませんでした(つかみ無しで投稿します): {e}")
        return ""
    text = text.strip()
    # 全体が「」や "" で囲まれているときだけ外す(文中の「」は残す。10-26 見本で先頭の「が消えたため)
    if len(text) > 2 and text[0] + text[-1] in ("「」", '""'):
        text = text[1:-1].strip()
    text = "\n".join(l.strip() for l in text.split("\n") if l.strip() and not l.strip().startswith("#"))
    if not text or len(text) > MAX_CHARS + 20:
        print(f"  つかみが長すぎる・空なので使いません: {text!r}")
        return ""
    try:   # AI が作ったとわかる文になっていないか(generate.py と同じ確認)
        from generate import check_ai_leak
        leak = check_ai_leak(text)
        if leak:
            print(f"  つかみに不自然な文があるので使いません: {leak}")
            return ""
    except Exception as e:
        print(f"  つかみの確認ができませんでした(つかみ無しで投稿します): {e}")
        return ""
    return text
