# Python App

Python CLIとDiscord Botのスターターです。Discord Botのトークンはコードやログに保存せず、Replit Secretsの`DISCORD_TOKEN`から実行時に読み込みます。

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

Botが起動したら、Discord上で`/ping`を実行すると`Pong!`を返します。
`/trade`を実行すると「取引記入」ボタンが表示されます。ボタンから「単体・まとめ買い」または「セット」を選択します。単体・まとめ買いではキャラ名、変異、レベル、個数、合計金額を入力し、個数1なら「単体」、2以上なら「まとめ買い」と自動判定します。セットでは最大3キャラのキャラ名・変異・レベル・個数を入力し、最後にセット全体の合計金額を登録します。
入力内容はSQLiteの`data/trades.sqlite3`へ保存し、確認メッセージを返します。セットの総額はセット取引にのみ保存し、各キャラの価格には分割しません。
`/trades`を実行すると、データベースを変更せずに最新20件の取引データを確認できます。旧レコードも表示されます。

Bot起動時にデータベースと必要なテーブルを自動作成し、旧スキーマの場合は登録済み取引を保持したまま安全に移行します。相場計算はまだ行いません。

Botが接続できない場合は、Discord Developer PortalでBotを作成し、対象サーバーへ`applications.commands`スコープ付きで招待しているか確認してください。トークン自体はチャット、ソースコード、ログへ貼り付けないでください。
起動時の接続は1回だけ試行します。Discord APIのレート制限が発生した場合は、原因を確認してから手動で再実行してください。

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
