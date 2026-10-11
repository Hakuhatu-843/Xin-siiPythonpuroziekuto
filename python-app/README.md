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

主な審査画面はGitHub Pagesの [`/admin.html`](https://hakuhatu-843.github.io/Xin-siiPythonpuroziekuto/admin.html) です。Workerに登録した審査専用キーが必要で、審査状態はD1から再読み込みできます。キーはページへ埋め込まず、入力後もページのメモリだけに保持してください。審査専用キーはレビューAPIだけに使い、Worker管理キーとは分けてください。

既存のDiscord審査通知・ボタンも互換機能として残っています。Workerに保存された申請をBotが2分間隔で古い順に取得し、設定済みチャンネルへ通知します。通知済みIDを記録して通常の再取得時の重複を防ぎます。Discord側の承認・拒否にはサーバー管理者または `WORKER_REVIEW_ROLE_ID` で指定した担当ロールが必要です。Worker側も `pending` からの1回の状態変更だけを許可し、担当者ラベルと日時をD1に記録します。

Botホストに以下をSecret/環境変数として設定してください。

- `DISCORD_TOKEN`: Discord Bot token
- `WORKER_ADMIN_KEY`: Worker の `ADMIN_KEY` と同じ値。公開コード・チャット・ログに記録しないこと
- `WORKER_REVIEW_KEY` は不要です。審査専用キーはWeb審査ページだけに設定します。
- `WORKER_REVIEW_ROLE_ID`: 任意。審査を許可するDiscordロールID。未設定ならサーバー管理者と `Manage Server` 権限者のみ操作可能

Bot起動後、審査チャンネルで `/set_trade_review_channel` を実行します。Botには対象チャンネルの閲覧・送信・埋め込みリンク権限が必要です。

旧 `/trade` コマンド、名前確認フロー、SQLiteの既存データは後方互換のため残っています。これらのローカルSQLite取引はWorker/D1のWeb取引・審査一覧とは別管理であり、D1の市場相場に自動同期されません。`/trades` は従来のローカルSQLite記録を表示します。

## 市場コマンド

- `/market`: Workerの正式キャラ一覧を読み込み、レアリティを選択してからキャラを選択し、市場スナップショットを表示します。Secretのように25体を超えるレアリティはページ切り替えに対応します。
- `/character_number`: Workerのキャラ一覧を使い、英語名と番号の一覧を全体メッセージで表示します。25体ずつページ切り替えできます。
- Botの番号・英語名・レアリティはWorkerの `/characters` を参照するため、Discord側に別の番号リストを持ちません。市場対象外の管理用キャラは表示対象から除外されます。

価格不足、未登録番号、API障害、タイムアウトはユーザー向けエラーとして表示します。Workerの `/characters` と `/market-snapshot` が利用可能である必要があります。
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
