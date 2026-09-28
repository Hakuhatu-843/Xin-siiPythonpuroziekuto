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

Botが接続できない場合は、Discord Developer PortalでBotを作成し、対象サーバーへ`applications.commands`スコープ付きで招待しているか確認してください。トークン自体はチャット、ソースコード、ログへ貼り付けないでください。

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
