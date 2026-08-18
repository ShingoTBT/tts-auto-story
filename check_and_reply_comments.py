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
from pathlib import Path

import yaml
import requests
import anthropic

from generate import load_system_prompt
from rakuten_product_search import search_product

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


def get_comment_count(api_key: str, post_id: str, account_id: str) -> int:
    headers = {"Authorization": f"Bearer {api_key}"}
    total = 0
    cursor = None

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

        pagination = data.get("pagination", {})
        if pagination.get("hasMore") and pagination.get("cursor"):
            cursor = pagination["cursor"]
        else:
            break

    return total


def post_reply_comment(api_key: str, post_id: str, account_id: str, text: str) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "accountId": account_id,
        "message": text,
    }
    r = requests.post(f"{ZERNIO_API_BASE}/inbox/comments/{post_id}", headers=headers, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def extract_topic_keyword(post_text: str, model: str) -> str:
    """投稿本文から、楽天で実際に検索してヒットしそうな商品カテゴリ名をClaudeに考えさせる"""
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
        process_account(config_path)


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
            count = get_comment_count(api_key, post_id, account_id)
        except Exception as e:
            print(f"コメント数取得エラー (post_id={post_id}): {e}")
            continue

        print(f"post_id={post_id}: コメント数={count}")

        if count <= threshold:
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

            product = search_product(keyword)
            if not product:
                print("関連商品が見つからなかったため、今回はコメントをスキップします")
                continue
        except Exception as e:
            print(f"商品検索処理でエラー (post_id={post_id}): {e} — この投稿はスキップして次に進みます")
            continue

        comment_text = f"{keyword}といえば、やっぱりこれだよね！\n　↓↓ad\n{product['url']}"

        try:
            post_reply_comment(api_key, post_id, account_id, comment_text)
            print(f"コメント投稿完了 (post_id={post_id})")
            record["commented"] = True
            updated = True

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
        rewrite_posted_threads(str(log_path), records)
        print("投稿ログを更新しました")


if __name__ == "__main__":
    main()
