# 毎日の点検（自動投稿の仕組み）

2026-11-15〜。オーナーの希望「Claude が1日1回必ずチェックし、異常があれば改善する流れに」。
Claude のクラウドの定期実行（毎朝9:00 JST）がこの手順で点検する。PC が起動していなくても動く。
手動で点検するときも、この手順でよい。
※ このリポジトリは公開されている。ここや記録にキー・トークン・個人の情報を書かない。

## 守ること（いちばん大事）
- **何かを直すと別の何かが抜ける、は絶対にやめる**。直すときは、まわりの決まり（プロンプト・確認の処理・ワークフローの条件）が抜けないか必ず確かめる
- 投稿・コメントは公式API（Zernio 経由）以外で操作しない。手で投稿しない。投稿・コメントを消さない
- 同じ内容の二重投稿・二重コメントは厳禁
- 試しに AI を何度も呼んでお金を使わない（あるログ・原稿で確かめる）
- 報告は日本語で、専門用語を避けて短く

## 準備
1. `git pull`（毎時、自動のコミットが入っている）
2. 直近の点検記録 `docs/daily_check_log.md` の先頭数件を読む（前回の続き・様子見の項目を引き継ぐ）

## 見ること（前日 0:00〜24:00 JST と、今朝ここまで）
1. **投稿の本数**（予定と比べる。足りない回は時刻まで特定する。多すぎる＝二重投稿も異常）
   - ニュース：1・6・11・16・21時の5本 … `outputs/account2_news/posted_videos.jsonl`
   - クセツヨ（TikTok）：1・6・11・16・21時の5本 … `outputs/account5_tiktok/posted_videos.jsonl`
   - 涙あふれる：2・12・22時の3本 … `outputs/account6_tiktok/posted_videos.jsonl`
   - Threads 子育て：毎時0分の24本 … `outputs/account3_parenting/posted_threads.jsonl`
   - Threads クセツヨ：毎時15分の24本 … `outputs/account4_threads/posted_threads.jsonl`（Threads は85%未満を異常とする）
   - posted_at はタイムゾーン付き。JST に直して数える
2. **抜けた回の理由**
   - `outputs/*/debug_failed_<日時>.txt`（原稿が決まりに通らず止まった回。日時は UTC）
   - GitHub の実行記録：公開リポジトリなので `https://api.github.com/repos/ShingoTBT/tts-auto-story/actions/workflows/<ワークフロー>/runs?per_page=30` で一覧が読める（gh が使えれば `gh run list`／`gh run view --log`）
   - **原稿づくりで止まった回は GitHub 上「成功」と出る**。本数との突き合わせで見つける
3. **失敗した実行**：同じ API の `status=failure`（前日分）
4. **コメント返し**（`reply_comments_tiktok.py`）：`outputs/*/comment_replies.jsonl` で、同じ comment_id が retry なしで2回以上あれば重複の疑い。retry 付きが急に増えていないか
5. **Threads の広告コメント**（`check_and_reply_comments.py`）：同じ投稿に2回付いていないか（記録のファイルで確認）
6. **監視**：`outputs/monitor_*.json`、ニュースの記録の `elevenlabs_remaining`（声の残り）

## 異常があったとき
- 原因がはっきりしていて、直し方が小さく安全なもの（決まりの書き漏らし・数え方の誤り・明らかな不具合）は、**直す → 手元で確かめる（python -m py_compile、関係する関数を実データで動かす等）→ main に push**
  - コミットメッセージは日本語で、何が起きて何を直したか。最後に `Co-Authored-By: Claude <noreply@anthropic.com>`
  - push が衝突したら `git pull --rebase` してやり直す（自動コミットが頻繁に入る）
- 次のものは**直さずに提案として記録**する：投稿の中身・方針・お金がかかる変更、投稿やコメントを消す・手で投稿する、サーバー（Xserver）の cron・キー（GitHub Secrets）の変更、原因がはっきりしないもの、大きな作り替え

## 終わったら
- `docs/daily_check_log.md` の**先頭**（見出しの下）に、その日の記録を足して push：
  ```
  ## 2026-11-16（月）
  - 本数：ニュース 5/5・クセツヨ 5/5・涙 3/3・Threads子育て 24/24・Threadsクセツヨ 23/24
  - 異常：（なし／何が起きたか）
  - 直したこと：（commit の短い ID と内容）
  - オーナーに決めてほしいこと：（あれば）
  ```
- 最後の返事にも同じ内容を短く書く
