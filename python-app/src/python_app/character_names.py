"""Character-name catalog and review persistence for the Discord bot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
import unicodedata


@dataclass(frozen=True)
class CharacterNameResolution:
    """The stored name and verification state for one submitted name."""

    input_name: str
    character_name: str
    normalized_name: str
    is_verified: bool


@dataclass(frozen=True)
class UnverifiedNameReview:
    """One unresolved character name submitted with a trade."""

    id: int
    trade_id: int
    position: int
    input_name: str
    normalized_name: str
    submitted_by_discord_user_id: str
    submitted_by_display_name: str
    submitted_at: str
    status: str
    notification_message_id: str | None
    resolved_canonical_name: str | None


def normalize_character_name(name: str) -> str:
    """Normalize equivalent Unicode spellings for catalog lookups."""
    return unicodedata.normalize("NFKC", name.strip()).casefold()


def initialize_character_name_schema(connection: sqlite3.Connection) -> None:
    """Add name-review fields and catalog tables without rewriting old trades."""
    trade_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(trades)")
    }
    if "is_verified" not in trade_columns:
        connection.execute(
            """
            ALTER TABLE trades
            ADD COLUMN is_verified INTEGER NOT NULL DEFAULT 1
                CHECK (is_verified IN (0, 1))
            """
        )

    character_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(trade_characters)")
    }
    if "normalized_name" not in character_columns:
        connection.execute(
            "ALTER TABLE trade_characters ADD COLUMN normalized_name TEXT NOT NULL DEFAULT ''"
        )
    if "is_verified" not in character_columns:
        connection.execute(
            """
            ALTER TABLE trade_characters
            ADD COLUMN is_verified INTEGER NOT NULL DEFAULT 1
                CHECK (is_verified IN (0, 1))
            """
        )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            created_by_discord_user_id TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS character_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_name TEXT NOT NULL,
            normalized_alias TEXT NOT NULL UNIQUE,
            character_id INTEGER NOT NULL
                REFERENCES characters(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            created_by_discord_user_id TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS character_name_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            input_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            submitted_by_discord_user_id TEXT NOT NULL,
            submitted_by_display_name TEXT NOT NULL DEFAULT '',
            submitted_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'ignored', 'resolved')),
            notification_message_id TEXT,
            resolved_canonical_name TEXT,
            reviewed_by_discord_user_id TEXT,
            reviewed_at TEXT,
            UNIQUE (trade_id, position)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_character_aliases_character
        ON character_aliases(character_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_character_name_reviews_status
        ON character_name_reviews(status, notification_message_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trade_characters_normalized_name
        ON trade_characters(normalized_name, is_verified)
        """
    )

    # Old detail rows predate the catalog and remain trusted; only new unknown
    # names are marked unverified. Fill the lookup key without changing names.
    rows = connection.execute(
        """
        SELECT id, character_name
        FROM trade_characters
        WHERE normalized_name = ''
        """
    ).fetchall()
    connection.executemany(
        "UPDATE trade_characters SET normalized_name = ? WHERE id = ?",
        [
            (normalize_character_name(str(name)), int(row_id))
            for row_id, name in rows
        ],
    )


def resolve_character_name(
    connection: sqlite3.Connection,
    input_name: str,
) -> CharacterNameResolution:
    """Resolve an exact canonical name or alias; never guess unknown names."""
    submitted_name = input_name.strip()
    normalized_name = normalize_character_name(submitted_name)
    if not normalized_name:
        raise ValueError("キャラ名を入力してください。")

    canonical = connection.execute(
        "SELECT canonical_name FROM characters WHERE normalized_name = ?",
        (normalized_name,),
    ).fetchone()
    if canonical is not None:
        return CharacterNameResolution(
            submitted_name,
            str(canonical[0]),
            normalized_name,
            True,
        )

    alias = connection.execute(
        """
        SELECT characters.canonical_name
        FROM character_aliases
        JOIN characters ON characters.id = character_aliases.character_id
        WHERE character_aliases.normalized_alias = ?
        """,
        (normalized_name,),
    ).fetchone()
    if alias is not None:
        return CharacterNameResolution(
            submitted_name,
            str(alias[0]),
            normalized_name,
            True,
        )

    return CharacterNameResolution(
        submitted_name,
        submitted_name,
        normalized_name,
        False,
    )


def create_name_review(
    connection: sqlite3.Connection,
    *,
    trade_id: int,
    position: int,
    resolution: CharacterNameResolution,
    submitted_by_discord_user_id: str,
    submitted_by_display_name: str,
    submitted_at: str,
) -> UnverifiedNameReview:
    """Queue an unknown character name for an administrator to review."""
    cursor = connection.execute(
        """
        INSERT INTO character_name_reviews (
            trade_id, position, input_name, normalized_name,
            submitted_by_discord_user_id, submitted_by_display_name,
            submitted_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_id,
            position,
            resolution.input_name,
            resolution.normalized_name,
            submitted_by_discord_user_id,
            submitted_by_display_name,
            submitted_at,
        ),
    )
    review = get_name_review(connection, int(cursor.lastrowid))
    if review is None:
        raise RuntimeError("未確認名の確認レコードを取得できませんでした。")
    return review


def _review_from_row(row: sqlite3.Row | tuple[object, ...]) -> UnverifiedNameReview:
    return UnverifiedNameReview(
        id=int(row[0]),
        trade_id=int(row[1]),
        position=int(row[2]),
        input_name=str(row[3]),
        normalized_name=str(row[4]),
        submitted_by_discord_user_id=str(row[5]),
        submitted_by_display_name=str(row[6]),
        submitted_at=str(row[7]),
        status=str(row[8]),
        notification_message_id=(
            str(row[9]) if row[9] is not None else None
        ),
        resolved_canonical_name=(
            str(row[10]) if row[10] is not None else None
        ),
    )


_REVIEW_SELECT = """
    SELECT id, trade_id, position, input_name, normalized_name,
           submitted_by_discord_user_id, submitted_by_display_name,
           submitted_at, status, notification_message_id,
           resolved_canonical_name
    FROM character_name_reviews
"""


def get_name_review(
    connection: sqlite3.Connection,
    review_id: int,
) -> UnverifiedNameReview | None:
    row = connection.execute(
        f"{_REVIEW_SELECT} WHERE id = ?",
        (review_id,),
    ).fetchone()
    return _review_from_row(row) if row is not None else None


def get_pending_name_reviews(
    connection: sqlite3.Connection,
    *,
    notification_missing: bool = False,
) -> list[UnverifiedNameReview]:
    query = f"{_REVIEW_SELECT} WHERE status = 'pending'"
    if notification_missing:
        query += " AND notification_message_id IS NULL"
    rows = connection.execute(query + " ORDER BY id").fetchall()
    return [_review_from_row(row) for row in rows]


def set_review_notification_message(
    connection: sqlite3.Connection,
    review_id: int,
    message_id: str,
) -> None:
    connection.execute(
        """
        UPDATE character_name_reviews
        SET notification_message_id = ?
        WHERE id = ? AND status = 'pending'
        """,
        (message_id, review_id),
    )


def set_unverified_channel(
    connection: sqlite3.Connection,
    channel_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO bot_settings (setting_key, setting_value)
        VALUES ('unverified_name_channel_id', ?)
        ON CONFLICT(setting_key)
        DO UPDATE SET setting_value = excluded.setting_value
        """,
        (channel_id,),
    )


def get_unverified_channel(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        """
        SELECT setting_value FROM bot_settings
        WHERE setting_key = 'unverified_name_channel_id'
        """
    ).fetchone()
    return str(row[0]) if row is not None else None


def _resolve_pending_name(
    connection: sqlite3.Connection,
    normalized_name: str,
    canonical_name: str,
) -> None:
    affected_trade_rows = connection.execute(
        """
        SELECT DISTINCT trade_id
        FROM trade_characters
        WHERE normalized_name = ? AND is_verified = 0
        """,
        (normalized_name,),
    ).fetchall()
    trade_ids = [int(row[0]) for row in affected_trade_rows]
    connection.execute(
        """
        UPDATE trade_characters
        SET character_name = ?, normalized_name = ?, is_verified = 1
        WHERE normalized_name = ? AND is_verified = 0
        """,
        (
            canonical_name,
            normalize_character_name(canonical_name),
            normalized_name,
        ),
    )
    for trade_id in trade_ids:
        transaction = connection.execute(
            "SELECT transaction_type FROM trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
        if transaction is None:
            continue
        if str(transaction[0]) != "セット":
            connection.execute(
                "UPDATE trades SET character_name = ? WHERE id = ?",
                (canonical_name, trade_id),
            )
        remaining_unverified = connection.execute(
            """
            SELECT 1 FROM trade_characters
            WHERE trade_id = ? AND is_verified = 0
            LIMIT 1
            """,
            (trade_id,),
        ).fetchone()
        connection.execute(
            "UPDATE trades SET is_verified = ? WHERE id = ?",
            (0 if remaining_unverified is not None else 1, trade_id),
        )

    connection.execute(
        """
        UPDATE character_name_reviews
        SET status = 'resolved',
            resolved_canonical_name = ?,
            reviewed_at = ?
        WHERE normalized_name = ? AND status != 'resolved'
        """,
        (
            canonical_name,
            datetime.now(timezone.utc).isoformat(),
            normalized_name,
        ),
    )


def register_canonical_name(
    connection: sqlite3.Connection,
    canonical_name: str,
    registered_by_discord_user_id: str,
) -> str:
    name = canonical_name.strip()
    normalized_name = normalize_character_name(name)
    if not normalized_name:
        raise ValueError("正式名称を入力してください。")

    existing_canonical = connection.execute(
        "SELECT canonical_name FROM characters WHERE normalized_name = ?",
        (normalized_name,),
    ).fetchone()
    if existing_canonical is None:
        alias = connection.execute(
            """
            SELECT characters.canonical_name
            FROM character_aliases
            JOIN characters ON characters.id = character_aliases.character_id
            WHERE character_aliases.normalized_alias = ?
            """,
            (normalized_name,),
        ).fetchone()
        if alias is not None:
            raise ValueError(
                f"この名前は「{alias[0]}」の別名として登録済みです。"
            )
        connection.execute(
            """
            INSERT INTO characters (
                canonical_name, normalized_name, created_at,
                created_by_discord_user_id
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                name,
                normalized_name,
                datetime.now(timezone.utc).isoformat(),
                registered_by_discord_user_id,
            ),
        )
        canonical = name
    else:
        canonical = str(existing_canonical[0])

    _resolve_pending_name(connection, normalized_name, canonical)
    return canonical


def register_character_alias(
    connection: sqlite3.Connection,
    alias_name: str,
    canonical_name: str,
    registered_by_discord_user_id: str,
) -> str:
    alias = alias_name.strip()
    canonical_input = canonical_name.strip()
    normalized_alias = normalize_character_name(alias)
    normalized_canonical = normalize_character_name(canonical_input)
    if not normalized_alias or not normalized_canonical:
        raise ValueError("別名と正式名称の両方を入力してください。")

    canonical_row = connection.execute(
        """
        SELECT id, canonical_name
        FROM characters WHERE normalized_name = ?
        """,
        (normalized_canonical,),
    ).fetchone()
    if canonical_row is None:
        canonical_alias = connection.execute(
            """
            SELECT characters.canonical_name
            FROM character_aliases
            JOIN characters ON characters.id = character_aliases.character_id
            WHERE character_aliases.normalized_alias = ?
            """,
            (normalized_canonical,),
        ).fetchone()
        if canonical_alias is not None:
            raise ValueError(
                f"入力した正式名称は「{canonical_alias[0]}」の別名です。"
            )
        connection.execute(
            """
            INSERT INTO characters (
                canonical_name, normalized_name, created_at,
                created_by_discord_user_id
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                canonical_input,
                normalized_canonical,
                datetime.now(timezone.utc).isoformat(),
                registered_by_discord_user_id,
            ),
        )
        canonical_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        canonical = canonical_input
        _resolve_pending_name(connection, normalized_canonical, canonical)
    else:
        canonical_id = int(canonical_row[0])
        canonical = str(canonical_row[1])

    canonical_collision = connection.execute(
        """
        SELECT id, canonical_name FROM characters
        WHERE normalized_name = ?
        """,
        (normalized_alias,),
    ).fetchone()
    if canonical_collision is not None and int(canonical_collision[0]) != canonical_id:
        raise ValueError(
            f"「{alias}」は別の正式名称として登録済みです。"
        )

    existing_alias = connection.execute(
        """
        SELECT character_id FROM character_aliases
        WHERE normalized_alias = ?
        """,
        (normalized_alias,),
    ).fetchone()
    if existing_alias is not None and int(existing_alias[0]) != canonical_id:
        existing_name = connection.execute(
            "SELECT canonical_name FROM characters WHERE id = ?",
            (int(existing_alias[0]),),
        ).fetchone()[0]
        raise ValueError(f"「{alias}」は「{existing_name}」の別名として登録済みです。")
    if existing_alias is None and normalized_alias != normalized_canonical:
        connection.execute(
            """
            INSERT INTO character_aliases (
                alias_name, normalized_alias, character_id, created_at,
                created_by_discord_user_id
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                alias,
                normalized_alias,
                canonical_id,
                datetime.now(timezone.utc).isoformat(),
                registered_by_discord_user_id,
            ),
        )

    _resolve_pending_name(connection, normalized_alias, canonical)
    return canonical


def ignore_name_review(
    connection: sqlite3.Connection,
    review_id: int,
    reviewed_by_discord_user_id: str,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE character_name_reviews
        SET status = 'ignored',
            reviewed_by_discord_user_id = ?,
            reviewed_at = ?
        WHERE id = ? AND status = 'pending'
        """,
        (
            reviewed_by_discord_user_id,
            datetime.now(timezone.utc).isoformat(),
            review_id,
        ),
    )
    return cursor.rowcount == 1