#!/usr/bin/env python3
"""
account3(Threads)の当日・前日分の投稿について、コメント数を確認し、
しきい値(デフォルト20件)を超えていて未対応のものに、楽天商品リンク付きの
コメントを自動投稿する。

使い方:
    python check_and_reply_comments.py accounts/account3_threads.yaml
"""

import sys
import os
import json
import datetime
import random
from pathlib import Path

import yaml
import requests
import anthropic

from generate import load_system_prompt
from product_search import search_product

ZERNIO_API_BASE = "https://zernio.com/api/v1"


def load_account_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_posted_threads(log_path: str) -> list[dict]:
    path = Path(log_path)
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def rewrite_posted_threads(log_path: str, records: list[dict]) -> None:
    with open(log_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def is_within_check_window(posted_at_str: str, days: int) -> bool:
    posted_at = datetime.datetime.fromisoformat(posted_at_str)
    now = datetime.datetime.now()
    return (now - posted_at) <= datetime.timedelta(days=days)


def get_comment_count(api_key: str, post_id: str, account_id: str, max_pages: int = 8, early_exit_threshold: int = None) -> int:
    """
    コメント数を取得する。バズった投稿は数百〜数千件になることがあり、
    全件をページネーションで数え続けると処理が非常に重くなるため、
    最大ページ数で打ち切る(それでもしきい値判定には十分な件数を確保できる)。

    early_exit_threshold を指定すると、その件数を超えた時点で即座に打ち切る
    (「しきい値を超えているかどうか」だけが分かれば十分な場面で高速化するため)。
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    total = 0
    cursor = None
    pages = 0

    while True:
        params = {"accountId": account_id}
        if cursor:
            params["cursor"] = cursor

        r = requests.get(
            f"{ZERNIO_API_BASE}/inbox/comments/{post_id}",
            headers=headers,
            params=params,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        comments = data.get("comments", data.get("data", []))
        total += len(comments)
        pages += 1

        if early_exit_threshold is not None and total > early_exit_threshold:
            break

        pagination = data.get("pagination", {})
        if pages >= max_pages:
            # 上限に達した時点で打ち切る(すでにしきい値は大きく超えている想定)
            break
        if pagination.get("hasMore") and pagination.get("cursor"):
            cursor = pagination["cursor"]
        else:
            break

    return total


def has_own_comment(api_key: str, post_id: str, account_id: str, own_username: str) -> bool:
    """
    この投稿に、指定アカウント自身によるコメントが既についているかを確認する。
    (自動投稿・手動投稿を問わず、二重コメントを防ぐため)

    運用上、自分でコメントする際は必ずピン留めしているため、
    自分のコメントは常に一覧の先頭に来る想定。そのため1ページ目だけ確認すれば十分で、
    バズった投稿でも全件ページネーションする必要がない。
    """
    if not own_username:
        return False

    headers = {"Authorization": f"Bearer {api_key}"}
    own_username_clean = own_username.lstrip("@").lower()

    r = requests.get(
        f"{ZERNIO_API_BASE}/inbox/comments/{post_id}",
        headers=headers,
        params={"accountId": account_id},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    comments = data.get("comments", data.get("data", []))

    for c in comments:
        author = str(
            c.get("username")
            or c.get("from", {}).get("username", "")
            or c.get("authorUsername", "")
        ).lstrip("@").lower()
        if author and author == own_username_clean:
            return True

    return False


def post_reply_comment(api_key: str, post_id: str, account_id: str, text: str, suppress_preview: bool = False) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "accountId": account_id,
        "message": text,
    }
    if suppress_preview:
        # Meta公式APIのlink_attachmentパラメータに相当する制御を試みる
        # (Zernio側がこれらのいずれかに対応していることを期待して複数候補を送る)
        payload["linkAttachment"] = None
        payload["disableLinkPreview"] = True
        payload["suppressPreview"] = True
    r = requests.post(f"{ZERNIO_API_BASE}/inbox/comments/{post_id}", headers=headers, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def extract_topic_keyword(post_text: str, model: str) -> str:
    """投稿本文から、楽天で実際に検索してヒットしそうな商品カテゴリ名をClaudeに考えさせる(検索用)"""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=30,
        system=(
            "与えられた投稿の内容(人間関係の悩み・シチュエーション)を読み、"
            "その話題に関連していて、かつ楽天市場で実際に検索すれば商品がヒットしそうな、"
            "具体的な商品カテゴリ名を1つ考えて出力してください。"
            "例：「引越しの手伝いへのお礼が一言だけだった」という話題なら「お礼ギフト」、"
            "「結婚式のご祝儀」の話題なら「ご祝儀袋」、"
            "「職場の人間関係」の話題なら「ストレス解消グッズ」のように、"
            "話題そのものではなく、話題から連想される“実在の商品ジャンル”を答えること。"
            "出力は10文字以内の商品カテゴリ名のみ。説明・記号・Markdown記法・改行を一切含めないこと。"
        ),
        messages=[{"role": "user", "content": post_text[:500]}],
    )
    parts = [b.text for b in response.content if b.type == "text"]
    keyword = "".join(parts).strip()
    # 安全策: 万一長い出力が返ってきても、先頭部分だけに切り詰める
    keyword = keyword.split("\n")[0].strip()
    if len(keyword) > 20:
        keyword = keyword[:20]
    return keyword


def generate_comment_phrase(post_text: str, product_name: str, model: str) -> str:
    """
    実際に見つかった商品名と投稿内容から、コメントの1行目(導入文)を丸ごと生成する。
    「↓↓」の後に「#AD」表記を挟んでから貼るリンクの、前置きとなる部分に入る、投稿内容と商品の両方に自然に合った短い一言。

    実際の手動投稿を分析した結果に基づき、4つの型からコード側でランダムに1つ選び、
    その型を明示的に指定して生成する(AI任せのランダム選択だと偏りが出るため)。
    """
    style_options = [
        (
            "型A：自分の気持ち・行動",
            "投稿者本人が、自分の行動や気持ちとして語る型で書いてください。\n"
            "例：「次はこの黒い平服にしようかな」「これ握って気持ち落ち着けようかな」"
            "「こういうの見ると欲しくなってきた」\n"
            "【注意】誰か他人に向けた命令形・呼びかけ（「〜して」「〜だよね」と他人に語りかける形）"
            "にしてはいけません。自分自身の感想・行動・気持ちとして書くこと。\n"
            "NG例：「これ握って気持ち落ち着けて」（→ 誰かに命令している）"
        ),
        (
            "型B：相手への皮肉・あてつけ",
            "投稿に出てくる、理不尽な言動をした相手を、商品にたとえて軽く皮肉る型で書いてください。\n"
            "例：「その先輩って、まさにこれ…？？汗」「その考え方がベストなのかな…」"
        ),
        (
            "型C：ちょっとした比喩・ネタ的な一言",
            "商品を、投稿の状況に軽く例えたり、ボケたりする型で書いてください。文字通りの一致でなくてよい。\n"
            "例：「500円分のお菓子だとイメージとしてはこんな感じ？💦」"
        ),
        (
            "型D：短い感想＋タイトル調の一言",
            "投稿全体への短い感想を、少し他人事のような温度感で書く型で書いてください。\n"
            "例：「先輩がうざい後輩の話😅」「大人になってからの友情ってこういうものなのかな…😂」"
        ),
    ]
    style_name, style_instruction = random.choice(style_options)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=60,
        system=(
            "あなたはSNSコメントの導入文を考える担当です。\n"
            "「投稿の内容」と「実際に見つかった商品名」の両方を踏まえて、"
            "この後に商品リンクを貼る前置きとなる、短く自然な一言を考えてください。\n\n"
            "【最重要】このコメントは、投稿を書いた本人(投稿の中の「私」)が、"
            "自分の投稿に対して自分で追加コメントしている、という設定です。\n\n"
            f"今回は、以下の型で書いてください。\n\n【{style_name}】\n{style_instruction}\n\n"
            "その他のルール：\n"
            "・1〜2行、合計25文字程度までの短さにすること\n"
            "・語尾は「〜かも」「〜かな」「〜？」「…」「😅」「😂」など、自然な口語・絵文字を使ってよい\n"
            "・「ad」「PR」などの広告表記語は含めないこと(別途付与されるため)\n\n"
            "出力は導入文のみ。説明・記号・Markdown記法・「型A：」のようなラベルは一切含めないこと。"
        ),
        messages=[{
            "role": "user",
            "content": f"投稿の内容:\n{post_text[:500]}\n\n実際に見つかった商品名:\n{product_name}",
        }],
    )
    parts = [b.text for b in response.content if b.type == "text"]
    phrase = "".join(parts).strip()
    if len(phrase) > 60:
        phrase = phrase[:60]
    return phrase


def generate_travel_fallback_phrase(post_text: str, model: str) -> str:
    """
    関連商品が見つからなかった場合のフォールバック用。
    投稿内容に絡めて「旅にでも出ては？」という切り口の導入文を生成する。
    """
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=60,
        system=(
            "あなたはSNSコメントの導入文を考える担当です。\n"
            "このコメントは、投稿を書いた本人(投稿の中の「私」)が、自分の投稿に対して"
            "自分で追加コメントしている、という設定です。\n"
            "与えられた投稿の内容を踏まえて、「こんな気分の時は、いっそ旅にでも出ようかな」"
            "という趣旨の、**自分自身の気持ち・行動として**の短い導入文を1つ考えてください。\n\n"
            "【最重要】視点についての注意：\n"
            "誰か他人に向けた命令形・呼びかけ（「〜しては？」「〜してみて」と他人に語りかける形）"
            "にしてはいけません。**自分自身がそうしたい、という気持ちとして書くこと**。\n"
            "・NG例：「モヤモヤした時は、旅にでも出てみては？」（→ 誰かに提案している）\n"
            "・OK例：「こんな時は、いっそ旅にでも出ようかな」「気分転換に旅行でも計画しようかな」"
            "（→ 自分の気持ち・行動として書かれている）\n\n"
            "その他のルール：\n"
            "・投稿の具体的な状況(誰との、どんな出来事か)に軽く触れつつ、"
            "「気分転換に旅行でも」という自分の気持ちに自然につなげること\n"
            "・「〜かな」「〜ようかな」のように、自分の気持ち・行動を表す語尾を使い、"
            "毎回同じ言い回しに固定せず、自然なバリエーションをつけること\n"
            "・1〜2行、合計30文字程度までの短さにすること\n"
            "・「ad」「PR」などの広告表記語は含めないこと(別途付与されるため)\n\n"
            "例：\n"
            "投稿「引越しの手伝いのお礼がジュース1本だけだった」\n"
            "→「モヤモヤするし、いっそ旅にでも出ようかな」\n\n"
            "出力は導入文のみ。説明・記号・Markdown記法は一切含めないこと。"
        ),
        messages=[{"role": "user", "content": post_text[:500]}],
    )
    parts = [b.text for b in response.content if b.type == "text"]
    phrase = "".join(parts).strip()
    if len(phrase) > 60:
        phrase = phrase[:60]
    return phrase


def build_travel_fallback_link() -> str | None:
    """楽天トラベルのトップページへの、共通アフィリエイトIDを使ったリンクを組み立てる"""
    import urllib.parse
    affiliate_id = os.environ.get("RAKUTEN_AFFILIATE_ID")
    if not affiliate_id:
        return None
    target = "https://travel.rakuten.co.jp/"
    encoded = urllib.parse.quote(target, safe="")
    return f"https://hb.afl.rakuten.co.jp/hgc/{affiliate_id}/?pc={encoded}&m={encoded}"


def build_redirect_url(target_url: str) -> str:
    """プレビュー抑制用の中継リダイレクトを経由したURLを組み立てる"""
    import urllib.parse
    redirect_base = os.environ.get("LINK_REDIRECT_URL")
    redirect_secret = os.environ.get("LINK_REDIRECT_SECRET")
    if not redirect_base or not redirect_secret:
        # 未設定なら元のURLをそのまま返す(フォールバック)
        return target_url
    encoded = urllib.parse.quote(target_url, safe="")
    return f"{redirect_base}?secret={redirect_secret}&url={encoded}"


def send_chatwork_notification(text: str) -> None:
    token = os.environ.get("CHATWORK_API_TOKEN")
    room_id = os.environ.get("CHATWORK_ROOM_ID")
    if not token or not room_id:
        print("ChatWork認証情報が見つからないため、通知をスキップします")
        return
    r = requests.post(
        f"https://api.chatwork.com/v2/rooms/{room_id}/messages",
        headers={"X-ChatWorkToken": token},
        data={"body": text},
        timeout=15,
    )
    r.raise_for_status()


def main():
    if len(sys.argv) < 2:
        print("使い方: python check_and_reply_comments.py <account_config1.yaml> [account_config2.yaml ...]")
        sys.exit(1)

    for config_path in sys.argv[1:]:
        print(f"=== {config_path} ===")
        try:
            process_account(config_path)
        except Exception as e:
            print(f"[エラー] {config_path} の処理全体が失敗しました: {e} — 次のアカウントに進みます")


def _extract_own_username(config: dict) -> str:
    """設定のthreads_url(例: https://www.threads.com/@kusetsuyo.qa)からユーザー名を取り出す"""
    url = config.get("threads_url", "")
    if "@" in url:
        return url.split("@")[-1].strip("/")
    return ""


def process_account(config_path: str):
    config = load_account_config(config_path)
    api_key = os.environ[config.get("zernio_api_key_env", "ZERNIO_API_KEY")]
    account_id = os.environ[config["zernio_account_id_env"]]
    threshold = config.get("comment_threshold", 20)
    check_days = config.get("comment_check_days", 2)

    log_path = Path(config["output_dir"]) / "posted_threads.jsonl"
    records = load_posted_threads(str(log_path))

    updated = False

    for record in records:
        if record.get("commented"):
            continue
        if not record.get("post_id"):
            continue
        if not is_within_check_window(record["posted_at"], check_days):
            continue

        post_id = record["post_id"]

        try:
            count = get_comment_count(api_key, post_id, account_id, early_exit_threshold=threshold)
        except Exception as e:
            print(f"コメント数取得エラー (post_id={post_id}): {e}")
            continue

        print(f"post_id={post_id}: コメント数={count}")

        if count <= threshold:
            continue

        # 既に自分自身がこの投稿にコメント済みでないか確認する
        # (自動・手動を問わず、二重コメントを防ぐため)
        own_username = _extract_own_username(config)
        try:
            if own_username and has_own_comment(api_key, post_id, account_id, own_username):
                print(f"post_id={post_id}: 既に自分のコメントが存在するためスキップします")
                record["commented"] = True
                updated = True
                rewrite_posted_threads(str(log_path), records)
                continue
        except Exception as e:
            print(f"既存コメント確認でエラー (post_id={post_id}): {e} — 念のためスキップします")
            continue

        try:
            # 元投稿本文を読み込み、商品検索キーワードを抽出
            text_file = record.get("text_file")
            if not text_file or not Path(text_file).exists():
                print(f"元テキストファイルが見つかりません: {text_file}")
                continue
            post_text = Path(text_file).read_text(encoding="utf-8")

            keyword = extract_topic_keyword(post_text, config["model"])
            print(f"商品検索キーワード: {keyword}")

            provider = os.environ.get("PRODUCT_SEARCH_PROVIDER", "amazon").lower().strip()
            product = search_product(keyword, provider)
            if product:
                comment_phrase = generate_comment_phrase(post_text, product["name"], config["model"])
                if provider == "rakuten":
                    # 楽天のみ、プレビュー抑制用の中継リダイレクトを経由する
                    # (Amazonは規約上、独自リダイレクトの使用が禁止されているため、素のリンクをそのまま使う)
                    link_url = build_redirect_url(product["url"])
                else:
                    link_url = product["url"]
            else:
                # 関連商品が見つからない場合は、楽天トラベルへのフォールバックリンクを使う
                # (フォールバック自体は常に楽天のリンクなので、プレビュー抑制の中継を使ってよい)
                print("関連商品が見つからなかったため、楽天トラベルへのフォールバックリンクを使用します")
                travel_link = build_travel_fallback_link()
                if not travel_link:
                    print("楽天トラベル用のアフィリエイトIDが未設定のため、今回はコメントをスキップします")
                    continue
                comment_phrase = generate_travel_fallback_phrase(post_text, config["model"])
                link_url = build_redirect_url(travel_link)

            print(f"コメント文言: {comment_phrase}")
        except Exception as e:
            print(f"商品検索処理でエラー (post_id={post_id}): {e} — この投稿はスキップして次に進みます")
            continue

        comment_text = f"{comment_phrase}\n　↓↓\n#AD\n{link_url}"

        try:
            post_reply_comment(api_key, post_id, account_id, comment_text)
            print(f"コメント投稿完了 (post_id={post_id})")
            record["commented"] = True
            updated = True

            # 1件成功するたびに、即座にファイルへ保存する(後続の処理で
            # エラーが起きても、この「済み」記録が失われないようにするため)
            rewrite_posted_threads(str(log_path), records)
            print("投稿ログを更新しました(即時保存)")

            chatwork_label = config.get("chatwork_label", config["account_name"])
            notify_text = (
                f"[info]コメント自動投稿：{chatwork_label}\n"
                f"コメント数が{threshold}件を超えたため、商品リンク付きコメントを投稿しました。[hr]"
                f"対象投稿:\n{post_text}\n\n"
                f"投稿したコメント:\n{comment_text}[/info]"
            )
            try:
                send_chatwork_notification(notify_text)
            except Exception as e:
                print(f"ChatWork通知エラー: {e}")
        except Exception as e:
            print(f"コメント投稿エラー (post_id={post_id}): {e}")

    if updated:
        print("このアカウントの処理が完了しました(すでに逐次保存済み)")


if __name__ == "__main__":
    main()
