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
`/trade`を実行すると「取引記入」ボタンが表示されます。ボタンを押すと、キャラ名・変異・個数・合計金額・取引タイプを入力するModalが開きます。送信後は入力内容の確認メッセージを返しますが、まだデータベースには保存しません。

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
