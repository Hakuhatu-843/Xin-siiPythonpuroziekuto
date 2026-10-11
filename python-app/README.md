# Python App

Python CLIとDiscord Botです。DiscordトークンやWorker管理キーはコードに保存せず、Botのホスティング環境にSecretとして設定します。

## 実行

```bash
cd python-app
PYTHONPATH=src python -m python_app
PYTHONPATH=src python -m python_app --name Hana
PYTHONPATH=src python -m python_app --name Hana --json
```

## Discord Botを起動

Replit Secretsに`DISCORD_TOKEN`を設定した状態で実行します。

```bash
cd python-app
PYTHONPATH=src python -m python_app.discord_bot
```

起動後は `/ping` で接続を確認できます。市場データはCloudflare Worker APIからD1のスナップショットを取得し、Bot側では計算しません。

## Web取引・審査

Webフォーム（リポジトリ直下の `index.html`）が主な取引入力経路です。番号登録済みキャラクターの一覧をWorkerから取得し、3桁番号・レベル・変異・個数・取引形式・価格を送ります。交換のみの場合は交換内容を価格と別に保存し、価格を作らず市場計算から除外します。受付取引はD1に `pending` で保存され、承認されるまで市場計算に使われません。再送時は同じ submission ID を使用して二重登録を防ぎます。

WorkerがD1に保存した未審査の価格取引と交換のみ取引は、Botが2分間隔で古い申請からページ単位で最後まで確認し、審査チャンネルに通知します。各巡回は開始時点の最大IDを上限として固定するため、取得中に届いた申請は次回巡回で確認されます。通知済みIDを記録して、pendingが続く申請の重複通知を防ぎます。サーバー管理者または `WORKER_REVIEW_ROLE_ID` で指定した担当ロールが承認・拒否できます。Worker側は `pending` からの1回の状態変更だけを許可し、担当者と日時をD1に記録します。

Botホストに以下をSecret/環境変数として設定してください。

- `DISCORD_TOKEN`: Discord Bot token
- `WORKER_ADMIN_KEY`: Worker の `ADMIN_KEY` と同じ値。公開コード・チャット・ログに記録しないこと
- `WORKER_REVIEW_ROLE_ID`: 任意。審査を許可するDiscordロールID。未設定ならサーバー管理者と `Manage Server` 権限者のみ操作可能

Bot起動後、審査チャンネルで `/set_trade_review_channel` を実行します。Botには対象チャンネルの閲覧・送信・埋め込みリンク権限が必要です。

旧 `/trade` コマンド、名前確認フロー、SQLiteの既存データは後方互換のため残っています。これらのローカルSQLite取引はWorker/D1のWeb取引・審査一覧とは別管理であり、D1の市場相場に自動同期されません。`/trades` は従来のローカルSQLite記録を表示します。

## 市場コマンド

- `/market character:001`: 番号からD1の市場スナップショットを表示
- `/market character:フェニックス`: 既存互換の名前指定。ローカルSQLiteの名前確認を通過した正式名のみ取得

価格不足、未登録番号、API障害、タイムアウトはユーザー向けエラーとして表示します。Workerの `/market-snapshot` と番号migrationが反映されている必要があります。

Botのトークンは従来のホスティング方針に従って管理してください。現在のREADMEではReplit Secretsを利用する例を示しています。Discord Developer PortalでBotを招待する場合は `applications.commands` を含めてください。

## インストールして使う場合

```bash
cd python-app
python -m pip install -e .
python-app --name Hana
python-discord-bot
```

## テスト

```bash
cd python-app
PYTHONPATH=src python -m unittest discover -s tests -v
```

Worker側のテストは `worker/README.md` を参照してください。
