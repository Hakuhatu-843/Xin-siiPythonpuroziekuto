from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "trades.sqlite3"

app = FastAPI(title="Trade Value API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


class TradeItem(BaseModel):
    rarity: str = Field(min_length=1, max_length=50)
    characterId: str = Field(min_length=1, max_length=100)
    level: int = Field(ge=1, le=301)
    mutation: str = Field(min_length=1, max_length=50)
    quantity: int = Field(ge=1, le=9999)


class TradePayload(BaseModel):
    tradeType: str = Field(min_length=1, max_length=30)
    items: list[TradeItem] = Field(min_length=1, max_length=3)
    totalPrice: int = Field(ge=1, le=2_000_000_000)


def get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_type TEXT NOT NULL,
                total_price INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected')),
                submitted_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trade_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL,
                position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 3),
                rarity TEXT NOT NULL,
                character_id TEXT NOT NULL,
                level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 301),
                mutation TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity >= 1),
                FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_trades_status
                ON trades(status);

            CREATE INDEX IF NOT EXISTS idx_trade_items_character
                ON trade_items(character_id);
            """
        )


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/trades", status_code=201)
def create_trade(payload: TradePayload) -> dict[str, Any]:
    if payload.tradeType not in {"single", "bundle", "set"}:
        raise HTTPException(status_code=400, detail="Invalid trade type")

    if payload.tradeType == "single" and len(payload.items) != 1:
        raise HTTPException(status_code=400, detail="Single trade must contain one item")

    if payload.tradeType in {"bundle", "set"} and not 1 <= len(payload.items) <= 3:
        raise HTTPException(status_code=400, detail="Trade must contain 1 to 3 items")

    submitted_at = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO trades (trade_type, total_price, status, submitted_at)
            VALUES (?, ?, 'pending', ?)
            """,
            (payload.tradeType, payload.totalPrice, submitted_at),
        )
        trade_id = cur.lastrowid

        conn.executemany(
            """
            INSERT INTO trade_items
                (trade_id, position, rarity, character_id, level, mutation, quantity)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    trade_id,
                    position,
                    item.rarity,
                    item.characterId,
                    item.level,
                    item.mutation,
                    item.quantity,
                )
                for position, item in enumerate(payload.items, start=1)
            ],
        )

    return {
        "ok": True,
        "tradeId": trade_id,
        "status": "pending",
        "submittedAt": submitted_at,
    }


@app.get("/trades/{trade_id}")
def get_trade(trade_id: int) -> dict[str, Any]:
    with get_db() as conn:
        trade = conn.execute(
            "SELECT id, trade_type, total_price, status, submitted_at FROM trades WHERE id = ?",
            (trade_id,),
        ).fetchone()

        if trade is None:
            raise HTTPException(status_code=404, detail="Trade not found")

        items = conn.execute(
            """
            SELECT position, rarity, character_id, level, mutation, quantity
            FROM trade_items
            WHERE trade_id = ?
            ORDER BY position
            """,
            (trade_id,),
        ).fetchall()

    return {
        "id": trade["id"],
        "tradeType": trade["trade_type"],
        "totalPrice": trade["total_price"],
        "status": trade["status"],
        "submittedAt": trade["submitted_at"],
        "items": [dict(item) for item in items],
    }
