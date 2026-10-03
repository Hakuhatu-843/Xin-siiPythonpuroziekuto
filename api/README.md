# Trade Value API

売買Webから送られた取引データをSQLiteへ保存するAPIです。

## エンドポイント

- GET /health
- POST /trades
- GET /trades/{trade_id}

新しく送信された取引は、すぐに相場へ反映せず `pending` として保存します。
後からDiscord側の審査機能で `approved` / `rejected` を管理する前提です。

## ローカル起動

```bash
pip install -r api/requirements.txt
uvicorn api.main:app --reload
```

SQLiteは `data/trades.sqlite3` に作成されます。

## POST例

```json
{
  "tradeType": "single",
  "items": [
    {
      "rarity": "legend",
      "characterId": "l001",
      "level": 100,
      "mutation": "none",
      "quantity": 1
    }
  ],
  "totalPrice": 50000
}
```
