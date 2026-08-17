# TTS自動ストーリー生成 - account1 (感動ストーリー)

## できること（現時点）
- 毎朝7:00(JST)、GitHub Actionsが自動起動
- Claude APIが感動ストーリーv3.0フレームワークに沿ってテキストを生成
- 出力は `outputs/account1_emotional/{日時}.txt` に保存され、リポジトリに自動コミットされる
- 既出タイトルは `logs/account1_emotional_titles.jsonl` に記録され、次回生成時の重複防止に使われる
- 出力フォーマットが崩れていた場合は自動で最大3回リトライする

## まだ未実装（次のステップ）
- 生成テキストを既存の `index.php` に自動投入して画像化するPuppeteerスクリプト
- 生成完了をSlack/Discordに通知する仕組み
- 通知から確認→スマホでワンタップ投稿する導線

## セットアップ手順

1. このフォルダをGitHubリポジトリにpushする
2. リポジトリの `Settings > Secrets and variables > Actions` で以下を登録
   - `ANTHROPIC_API_KEY` : Claude APIキー
3. `accounts/account1_emotional.yaml` の `image_tool_url` を、実際にXserver上にホストしている `index.php` のURLに書き換える
4. `Actions` タブから `account1_emotional_story` を選び、`Run workflow` で手動実行してみて、
   `outputs/account1_emotional/` に想定通りのテキストファイルが生成されるか確認する
5. 問題なければ、毎朝7:00(JST)に自動実行される状態になる

## 動作確認（ローカル）

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
python generate.py accounts/account1_emotional.yaml
```

## 複数アカウントへの拡張方法

1. `accounts/account2_xxx.yaml` を新規作成（`account1_emotional.yaml` をコピーして書き換え）
2. `prompts/xxx.md` に新しいジャンル用のシステムプロンプトを作成
3. `.github/workflows/account1_emotional.yaml` をコピーし、ファイル名・cron時刻・
   `accounts/account2_xxx.yaml` の参照先・Secrets名を変更
4. `generate.py` 本体は変更不要（アカウント設定を読み込むだけの共通処理のため）
