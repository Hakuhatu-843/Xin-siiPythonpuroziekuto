# 新しいPythonプロジェクト

標準ライブラリだけで動く、拡張しやすいPython CLIスターターです。

## Run & Operate

- `cd python-app && PYTHONPATH=src python -m python_app` — CLIを実行
- `cd python-app && PYTHONPATH=src python -m python_app.discord_bot` — `DISCORD_TOKEN`でDiscord Botを起動
- `cd python-app && PYTHONPATH=src python -m unittest discover -s tests -v` — Pythonテストを実行
- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string

## Stack

- Python 3.11+、`discord.py`
- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `python-app/pyproject.toml` — Pythonプロジェクト設定とCLIエントリーポイント
- `python-app/src/python_app/` — アプリケーション本体
- `python-app/src/python_app/discord_bot.py` — Replit Secretsからトークンを読むDiscord Bot
- `python-app/data/trades.sqlite3` — `/trade`の取引記入を保存するSQLiteデータベース（起動時に自動作成）
- `python-app/tests/` — 標準ライブラリのunittest
- `lib/api-spec/openapi.yaml` — API契約のソース

## Architecture decisions

- Pythonコードは`src`レイアウトにして、パッケージの境界を明確にする。
- 初期実装は標準ライブラリのみとし、用途が決まるまで依存関係を増やさない。
- 既存のTypeScript APIサーバーとPythonスターターは独立した構成にする。
- Discord Botのトークンは`DISCORD_TOKEN`環境変数から実行時に読み込み、トークンをログやエラーメッセージに含めない。
- Discord APIの429発生時に自動再接続せず、Workflowを停止して原因確認後に手動で再実行する。

## Product

- 名前を受け取り、挨拶を表示するCLIの最小サンプル。
- 通常出力とJSON出力に対応し、後からドメイン処理を追加できる。
- `/ping`スラッシュコマンドに応答するDiscord Bot。
- `/trade`で「取引記入」ボタンを表示し、Modalの入力内容をSQLiteへ保存して確認メッセージを返す。相場計算は行わない。

## User preferences

-

## Gotchas

- 直接実行する場合は`python-app`ディレクトリ内で`PYTHONPATH=src`を指定する。

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
