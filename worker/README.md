# Trade Value API (Cloudflare Workers + D1)

GitHub Pagesの取引入力フォームから受け取ったデータをCloudflare D1へ保存します。

## エンドポイント

- GET /health
- POST /trades
- GET /trades/{trade_id}

新規取引は `pending` として保存します。相場計算へ反映する前にDiscord側で審査する前提です。

## 初回セットアップ

1. CloudflareでD1データベースを作成し、データベースIDを `worker/wrangler.toml` の `database_id` に入れる。
2. D1へ `worker/schema.sql` を適用する。
3. `worker/` で `npx wrangler deploy` を実行する。
4. 発行された `https://....workers.dev` のURLを `index.html` の `API_BASE` に設定する。

## ローカル確認

WranglerでローカルD1を使う場合は、プロジェクトのWorker設定に従ってローカルDBへschemaを適用してからdev serverを起動してください。
