"""A minimal Discord bot that reads its token from Replit Secrets."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from urllib.parse import quote

import discord
from discord import app_commands

from .market_api import CharacterCatalogEntry, MarketAPIError, get_characters, get_market_snapshot
from .character_names import (
    CharacterNameResolution,
    UnverifiedNameReview,
    create_name_review,
    get_name_review,
    get_pending_name_reviews,
    get_unverified_channel,
    ignore_name_review,
    initialize_character_name_schema,
    normalize_character_name,
    register_canonical_name,
    register_character_alias,
    resolve_character_name,
    set_review_notification_message,
    set_unverified_channel,
)
from .trade_review_api import (
    ReviewExchange,
    ReviewTrade,
    TradeReviewAPIError,
    get_pending_exchanges,
    get_pending_trades,
    update_exchange_status,
    update_trade_status,
)

TOKEN_ENV_VAR = "DISCORD_TOKEN"
WORKER_ADMIN_KEY_ENV_VAR = "WORKER_ADMIN_KEY"
TRANSACTION_TYPES = ("通常", "単体", "まとめ買い", "セット")
DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "trades.sqlite3"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeCharacter:
    """Character details belonging to one trade transaction."""

    character_name: str
    mutation: str
    level: str
    quantity: int
    is_verified: bool = True


def initialize_database(db_path: str | Path = DATABASE_PATH) -> None:
    """Create or safely migrate the SQLite trade database."""
    database_path = Path(db_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path)
    try:
        # Rebuilding the parent table is necessary to extend its CHECK constraint.
        # Keep child rows untouched and re-enable FK checks before returning.
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_name TEXT NOT NULL,
                mutation TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                total_amount TEXT NOT NULL,
                transaction_type TEXT NOT NULL
                    CHECK (transaction_type IN ('通常', '単体', 'まとめ買い', 'セット')),
                registered_at TEXT NOT NULL,
                registered_by_discord_user_id TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT ''
            )
            """
        )

        schema_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'trades'"
        ).fetchone()
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(trades)").fetchall()
        }
        schema_sql = str(schema_row[0]) if schema_row else ""

        if "単体" not in schema_sql or "level" not in columns:
            level_expression = "COALESCE(level, '')" if "level" in columns else "''"
            connection.execute(
                """
                CREATE TABLE trades_migrating (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_name TEXT NOT NULL,
                    mutation TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    total_amount TEXT NOT NULL,
                    transaction_type TEXT NOT NULL
                        CHECK (transaction_type IN ('通常', '単体', 'まとめ買い', 'セット')),
                    registered_at TEXT NOT NULL,
                    registered_by_discord_user_id TEXT NOT NULL,
                    level TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                f"""
                INSERT INTO trades_migrating (
                    id, character_name, mutation, quantity, total_amount,
                    transaction_type, registered_at,
                    registered_by_discord_user_id, level
                )
                SELECT id, character_name, mutation, quantity, total_amount,
                       transaction_type, registered_at,
                       registered_by_discord_user_id, {level_expression}
                FROM trades
                """
            )
            connection.execute("DROP TABLE trades")
            connection.execute(
                "ALTER TABLE trades_migrating RENAME TO trades"
            )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL
                    REFERENCES trades(id) ON DELETE CASCADE,
                position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 3),
                character_name TEXT NOT NULL,
                mutation TEXT NOT NULL,
                level TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                normalized_name TEXT NOT NULL DEFAULT '',
                is_verified INTEGER NOT NULL DEFAULT 1,
                UNIQUE (trade_id, position)
            )
            """
        )

        # Older databases may have been created before verification columns
        # existed. Add the missing columns without deleting existing rows.
        trade_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(trades)").fetchall()
        }
        if "is_verified" not in trade_columns:
            connection.execute(
                "ALTER TABLE trades ADD COLUMN is_verified INTEGER NOT NULL DEFAULT 1"
            )

        character_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(trade_characters)"
            ).fetchall()
        }
        if "normalized_name" not in character_columns:
            connection.execute(
                "ALTER TABLE trade_characters ADD COLUMN normalized_name TEXT NOT NULL DEFAULT ''"
            )
        if "is_verified" not in character_columns:
            connection.execute(
                "ALTER TABLE trade_characters ADD COLUMN is_verified INTEGER NOT NULL DEFAULT 1"
            )

        initialize_character_name_schema(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS worker_trade_notifications (
                trade_id INTEGER PRIMARY KEY,
                discord_channel_id TEXT NOT NULL,
                discord_message_id TEXT NOT NULL,
                notified_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS worker_exchange_notifications (
                exchange_id INTEGER PRIMARY KEY,
                discord_channel_id TEXT NOT NULL,
                discord_message_id TEXT NOT NULL,
                notified_at TEXT NOT NULL
            )
            """
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.close()


def parse_quantity(value: str) -> int:
    """Convert the quantity field to a positive integer."""
    try:
        quantity = int(value.strip())
    except ValueError as error:
        raise ValueError("個数は1以上の整数で入力してください。") from error

    if quantity < 1:
        raise ValueError("個数は1以上の整数で入力してください。")

    return quantity


def transaction_type_for_quantity(quantity: int) -> str:
    """Infer single or bulk transaction type from its positive quantity."""
    if quantity < 1:
        raise ValueError("個数は1以上の整数で入力してください。")
    return "単体" if quantity == 1 else "まとめ買い"


def _insert_trade(
    connection: sqlite3.Connection,
    *,
    character_name: str,
    mutation: str,
    quantity: int,
    total_amount: str,
    transaction_type: str,
    registered_by_discord_user_id: str,
    registered_at: str,
    level: str,
    is_verified: bool,
    characters: Sequence[TradeCharacter],
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO trades (
            character_name,
            mutation,
            quantity,
            total_amount,
            transaction_type,
            registered_at,
            registered_by_discord_user_id,
            level,
            is_verified
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            character_name,
            mutation,
            quantity,
            total_amount,
            transaction_type,
            registered_at,
            registered_by_discord_user_id,
            level,
            int(is_verified),
        ),
    )
    trade_id = int(cursor.lastrowid)
    connection.executemany(
        """
        INSERT INTO trade_characters (
            trade_id, position, character_name, mutation, level, quantity,
            normalized_name, is_verified
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                trade_id,
                position,
                character.character_name,
                character.mutation,
                character.level,
                character.quantity,
                normalize_character_name(character.character_name),
                int(character.is_verified),
            )
            for position, character in enumerate(characters, start=1)
        ],
    )
    return trade_id


def save_trade_with_reviews(
    *,
    character_name: str,
    mutation: str,
    quantity: int,
    total_amount: str,
    transaction_type: str,
    registered_by_discord_user_id: str,
    db_path: str | Path = DATABASE_PATH,
    registered_at: str | None = None,
    level: str = "",
    submitted_by_display_name: str = "",
) -> tuple[int, list[UnverifiedNameReview]]:
    """Save one trade and queue an administrator review if its name is unknown."""
    initialize_database(db_path)
    timestamp = registered_at or datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        resolution = resolve_character_name(connection, character_name)
        character = TradeCharacter(
            character_name=resolution.character_name,
            mutation=mutation,
            level=level,
            quantity=quantity,
            is_verified=resolution.is_verified,
        )
        trade_id = _insert_trade(
            connection,
            character_name=resolution.character_name,
            mutation=mutation,
            quantity=quantity,
            total_amount=total_amount,
            transaction_type=transaction_type,
            registered_by_discord_user_id=registered_by_discord_user_id,
            registered_at=timestamp,
            level=level,
            is_verified=resolution.is_verified,
            characters=(character,),
        )

        reviews = []
        if not resolution.is_verified:
            reviews.append(
                create_name_review(
                    connection,
                    trade_id=trade_id,
                    position=1,
                    resolution=resolution,
                    submitted_by_discord_user_id=registered_by_discord_user_id,
                    submitted_by_display_name=submitted_by_display_name,
                    submitted_at=timestamp,
                )
            )

    return trade_id, reviews


def save_trade(
    *,
    character_name: str,
    mutation: str,
    quantity: int,
    total_amount: str,
    transaction_type: str,
    registered_by_discord_user_id: str,
    db_path: str | Path = DATABASE_PATH,
    registered_at: str | None = None,
    level: str = "",
) -> int:
    """Persist one trade entry and return its database ID."""
    trade_id, _ = save_trade_with_reviews(
        character_name=character_name,
        mutation=mutation,
        quantity=quantity,
        total_amount=total_amount,
        transaction_type=transaction_type,
        registered_by_discord_user_id=registered_by_discord_user_id,
        db_path=db_path,
        registered_at=registered_at,
        level=level,
    )
    return trade_id


def save_set_trade_with_reviews(
    *,
    characters: Sequence[TradeCharacter],
    total_amount: str,
    registered_by_discord_user_id: str,
    db_path: str | Path = DATABASE_PATH,
    registered_at: str | None = None,
    submitted_by_display_name: str = "",
) -> tuple[int, list[UnverifiedNameReview]]:
    """Save a set and queue a review for each unknown character name."""
    if not 1 <= len(characters) <= 3:
        raise ValueError("セットに登録できるキャラは1〜3体です。")
    if any(character.quantity < 1 for character in characters):
        raise ValueError("個数は1以上の整数で入力してください。")

    initialize_database(db_path)
    timestamp = registered_at or datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        resolutions = [
            resolve_character_name(connection, character.character_name)
            for character in characters
        ]
        resolved_characters = [
            TradeCharacter(
                character_name=resolution.character_name,
                mutation=character.mutation,
                level=character.level,
                quantity=character.quantity,
                is_verified=resolution.is_verified,
            )
            for character, resolution in zip(characters, resolutions)
        ]
        trade_id = _insert_trade(
            connection,
            character_name=f"セット取引（{len(characters)}キャラ）",
            mutation="—",
            quantity=sum(character.quantity for character in characters),
            total_amount=total_amount,
            transaction_type="セット",
            registered_by_discord_user_id=registered_by_discord_user_id,
            registered_at=timestamp,
            level="",
            is_verified=all(item.is_verified for item in resolved_characters),
            characters=resolved_characters,
        )
        reviews = [
            create_name_review(
                connection,
                trade_id=trade_id,
                position=position,
                resolution=resolution,
                submitted_by_discord_user_id=registered_by_discord_user_id,
                submitted_by_display_name=submitted_by_display_name,
                submitted_at=timestamp,
            )
            for position, resolution in enumerate(resolutions, start=1)
            if not resolution.is_verified
        ]

    return trade_id, reviews


def save_set_trade(
    *,
    characters: Sequence[TradeCharacter],
    total_amount: str,
    registered_by_discord_user_id: str,
    db_path: str | Path = DATABASE_PATH,
    registered_at: str | None = None,
) -> int:
    """Save a set transaction and its character details without unit prices."""
    trade_id, _ = save_set_trade_with_reviews(
        characters=characters,
        total_amount=total_amount,
        registered_by_discord_user_id=registered_by_discord_user_id,
        db_path=db_path,
        registered_at=registered_at,
    )
    return trade_id


def get_character_name_review(
    review_id: int,
    db_path: str | Path = DATABASE_PATH,
) -> UnverifiedNameReview | None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        return get_name_review(connection, review_id)


def get_pending_character_name_reviews(
    db_path: str | Path = DATABASE_PATH,
    *,
    notification_missing: bool = False,
) -> list[UnverifiedNameReview]:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        return get_pending_name_reviews(
            connection,
            notification_missing=notification_missing,
        )


def configure_unverified_name_channel(
    channel_id: str,
    db_path: str | Path = DATABASE_PATH,
) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        set_unverified_channel(connection, channel_id)


def get_configured_unverified_name_channel(
    db_path: str | Path = DATABASE_PATH,
) -> str | None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        return get_unverified_channel(connection)


def configure_worker_trade_review_channel(
    channel_id: str,
    db_path: str | Path = DATABASE_PATH,
) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO bot_settings (setting_key, setting_value)
            VALUES ('worker_trade_review_channel_id', ?)
            ON CONFLICT(setting_key)
            DO UPDATE SET setting_value = excluded.setting_value
            """,
            (channel_id,),
        )


def get_worker_trade_review_channel(
    db_path: str | Path = DATABASE_PATH,
) -> str | None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT setting_value FROM bot_settings
            WHERE setting_key = 'worker_trade_review_channel_id'
            """
        ).fetchone()
    return str(row[0]) if row is not None else None


def get_worker_trade_notification_ids(
    db_path: str | Path = DATABASE_PATH,
) -> list[tuple[int, str]]:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT trade_id, discord_message_id
            FROM worker_trade_notifications
            """
        ).fetchall()
    return [(int(trade_id), str(message_id)) for trade_id, message_id in rows]


def record_worker_trade_notification(
    trade_id: int,
    channel_id: str,
    message_id: str,
    db_path: str | Path = DATABASE_PATH,
) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO worker_trade_notifications (
                trade_id, discord_channel_id, discord_message_id, notified_at
            ) VALUES (?, ?, ?, ?)
            """,
            (trade_id, channel_id, message_id, datetime.now(timezone.utc).isoformat()),
        )


def get_worker_exchange_notification_ids(
    db_path: str | Path = DATABASE_PATH,
) -> list[tuple[int, str]]:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT exchange_id, discord_message_id
            FROM worker_exchange_notifications
            """
        ).fetchall()
    return [(int(exchange_id), str(message_id)) for exchange_id, message_id in rows]


def record_worker_exchange_notification(
    exchange_id: int,
    channel_id: str,
    message_id: str,
    db_path: str | Path = DATABASE_PATH,
) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO worker_exchange_notifications (
                exchange_id, discord_channel_id, discord_message_id, notified_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                exchange_id,
                channel_id,
                message_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def record_unverified_name_notification(
    review_id: int,
    message_id: str,
    db_path: str | Path = DATABASE_PATH,
) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        set_review_notification_message(connection, review_id, message_id)


def approve_unverified_name_as_canonical(
    review_id: int,
    administrator_id: str,
    db_path: str | Path = DATABASE_PATH,
) -> str:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        review = get_name_review(connection, review_id)
        if review is None or review.status != "pending":
            raise ValueError("この未確認名はすでに処理されています。")
        return register_canonical_name(
            connection,
            review.input_name,
            administrator_id,
        )


def approve_unverified_name_as_alias(
    review_id: int,
    canonical_name: str,
    administrator_id: str,
    db_path: str | Path = DATABASE_PATH,
) -> str:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        review = get_name_review(connection, review_id)
        if review is None or review.status != "pending":
            raise ValueError("この未確認名はすでに処理されています。")
        return register_character_alias(
            connection,
            review.input_name,
            canonical_name,
            administrator_id,
        )


def dismiss_unverified_name_review(
    review_id: int,
    administrator_id: str,
    db_path: str | Path = DATABASE_PATH,
) -> bool:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        return ignore_name_review(
            connection,
            review_id,
            administrator_id,
        )


def get_recent_trades(
    limit: int = 20,
    db_path: str | Path = DATABASE_PATH,
) -> list[dict[str, object]]:
    """Read the latest trades without creating or modifying the database."""
    if limit < 1:
        return []

    database_path = Path(db_path)
    if not database_path.exists():
        return []

    read_only_uri = (
        f"file:{quote(str(database_path.resolve()), safe='/')}?mode=ro"
    )
    with sqlite3.connect(read_only_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id,
                   character_name,
                   mutation,
                   level,
                   quantity,
                   total_amount,
                   transaction_type,
                   is_verified,
                   registered_at
            FROM trades
            ORDER BY id DESC
            LIMIT ?
            """,
            (min(limit, 20),),
        ).fetchall()

        trades: list[dict[str, object]] = []
        for row in rows:
            trade = dict(row)
            characters = connection.execute(
                """
                SELECT character_name, mutation, level, quantity, is_verified
                FROM trade_characters
                WHERE trade_id = ?
                ORDER BY position
                """,
                (trade["id"],),
            ).fetchall()
            if characters:
                trade["characters"] = [dict(character) for character in characters]
            else:
                # Legacy records predate character detail rows.
                trade["characters"] = [
                    {
                        "character_name": trade["character_name"],
                        "mutation": trade["mutation"],
                        "level": trade["level"],
                        "quantity": trade["quantity"],
                        "is_verified": trade["is_verified"],
                    }
                ]
            trades.append(trade)

    return trades


def _escape_for_discord(value: str) -> str:
    """Prevent user-entered values from creating mentions or markdown."""
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))


def format_trade_confirmation(
    character_name: str,
    mutation: str,
    quantity: str,
    total_amount: str,
    transaction_type: str,
    level: str = "",
) -> str:
    """Format a trade entry for confirmation without persisting it."""
    return "\n".join(
        [
            "取引内容を確認してください。",
            f"**キャラ名**: {_escape_for_discord(character_name)}",
            f"**変異**: {_escape_for_discord(mutation)}",
            f"**レベル**: {_escape_for_discord(level)}",
            f"**個数**: {_escape_for_discord(quantity)}",
            f"**合計金額**: {_escape_for_discord(total_amount)}",
            f"**取引タイプ**: {_escape_for_discord(transaction_type)}",
        ]
    )


def format_recent_trades(
    trades: list[dict[str, object]],
) -> discord.Embed:
    """Format recent trades for a Discord embed."""
    embed = discord.Embed(title="最新の取引データ", color=discord.Color.blurple())

    if not trades:
        embed.description = "保存されている取引データはありません。"
        return embed

    for index, trade in enumerate(trades, start=1):
        transaction_type = str(trade["transaction_type"])
        characters = trade.get("characters")
        if not isinstance(characters, list) or not characters:
            characters = [
                {
                    "character_name": trade["character_name"],
                    "mutation": trade["mutation"],
                    "level": trade.get("level", ""),
                    "quantity": trade["quantity"],
                }
            ]

        character_lines = []
        for character_index, character in enumerate(characters, start=1):
            if not isinstance(character, Mapping):
                continue
            name = _escape_for_discord(str(character["character_name"]))[:50]
            mutation = _escape_for_discord(str(character["mutation"]))[:40]
            level = _escape_for_discord(str(character.get("level", "")))[:20]
            quantity = _escape_for_discord(str(character["quantity"]))[:10]
            character_is_verified = bool(
                character.get("is_verified", trade.get("is_verified", True))
            )
            prefix = f"キャラ{character_index}" if transaction_type == "セット" else "キャラ名"
            details = f"{prefix}: {name} / 変異: {mutation}"
            if level:
                details += f" / レベル: {level}"
            details += f" / 個数: {quantity}"
            if not character_is_verified:
                details += " / ⚠ 未確認"
            character_lines.append(details)

        verification_line = (
            []
            if bool(trade.get("is_verified", True))
            else ["⚠ 未確認名あり（相場計算対象外）"]
        )
        value = "\n".join(
            [
                *character_lines,
                *verification_line,
                f"**合計金額**: {_escape_for_discord(str(trade['total_amount']))[:40]}",
                f"**取引タイプ**: {_escape_for_discord(transaction_type)}",
                f"**登録日時**: {_escape_for_discord(str(trade['registered_at']))[:40]}",
            ]
        )
        field_name = (
            f"{index}. セット取引（{len(characters)}キャラ）"
            if transaction_type == "セット"
            else f"{index}. {_escape_for_discord(str(trade['character_name']))[:80]}"
        )
        embed.add_field(
            name=field_name,
            value=value,
            inline=False,
        )

    return embed


def _is_guild_administrator(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def _can_review_worker_trades(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    if permissions and (permissions.administrator or permissions.manage_guild):
        return True
    reviewer_role_id = os.environ.get("WORKER_REVIEW_ROLE_ID", "").strip()
    if not reviewer_role_id:
        return False
    roles = getattr(interaction.user, "roles", ())
    return any(str(role.id) == reviewer_role_id for role in roles)


def format_unverified_name_notification(
    review: UnverifiedNameReview,
) -> discord.Embed:
    """Show an unknown name and its submitter without triggering mentions."""
    embed = discord.Embed(
        title="未確認のキャラ名",
        description="この名前を正式名称として登録するか、別名として登録してください。",
        color=discord.Color.orange(),
    )
    input_name = _escape_for_discord(review.input_name)[:200]
    user_name = (
        review.submitted_by_display_name
        or review.submitted_by_discord_user_id
    )
    user_value = (
        f"{_escape_for_discord(user_name)[:100]} "
        f"(ID: {_escape_for_discord(review.submitted_by_discord_user_id)[:30]})"
    )
    embed.add_field(name="入力された名前", value=input_name, inline=False)
    embed.add_field(name="入力したユーザー", value=user_value, inline=False)
    embed.add_field(
        name="登録日時",
        value=_escape_for_discord(review.submitted_at)[:50],
        inline=False,
    )
    return embed


class CharacterAliasModal(discord.ui.Modal, title="入力名を別名として登録"):
    """Ask an administrator for the canonical name behind an unknown alias."""

    canonical_name = discord.ui.TextInput(
        label="正式名称",
        placeholder="例: ケツァルコアトル",
        max_length=100,
    )

    def __init__(
        self,
        review_id: int,
        db_path: str | Path = DATABASE_PATH,
    ) -> None:
        super().__init__()
        self.review_id = review_id
        self.db_path = Path(db_path)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _is_guild_administrator(interaction):
            await interaction.response.send_message(
                "この操作はサーバー管理者のみ実行できます。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            canonical = approve_unverified_name_as_alias(
                self.review_id,
                str(self.canonical_name.value),
                str(interaction.user.id),
                db_path=self.db_path,
            )
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        except sqlite3.Error:
            logger.exception("キャラ名の別名登録に失敗しました。")
            await interaction.followup.send(
                "別名を登録できませんでした。時間をおいて再度お試しください。",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"「{_escape_for_discord(str(self.canonical_name.value))}」を正式名称として、"
            f"「{_escape_for_discord(canonical)}」を別名として登録しました。",
            ephemeral=True,
        )


class UnverifiedNameReviewView(discord.ui.View):
    """Persistent administrator actions attached to an unknown-name notice."""

    def __init__(
        self,
        review_id: int,
        db_path: str | Path = DATABASE_PATH,
    ) -> None:
        super().__init__(timeout=None)
        self.review_id = review_id
        self.db_path = Path(db_path)

    async def _require_pending_review(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if not _is_guild_administrator(interaction):
            await interaction.response.send_message(
                "この操作はサーバー管理者のみ実行できます。",
                ephemeral=True,
            )
            return False
        review = get_character_name_review(self.review_id, self.db_path)
        if review is None or review.status != "pending":
            await interaction.response.send_message(
                "この未確認名はすでに処理されています。",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="正式名称として登録",
        style=discord.ButtonStyle.success,
        custom_id="unverified_name:canonical",
    )
    async def register_canonical(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if not await self._require_pending_review(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            canonical = approve_unverified_name_as_canonical(
                self.review_id,
                str(interaction.user.id),
                db_path=self.db_path,
            )
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        except sqlite3.Error:
            logger.exception("正式名称の登録に失敗しました。")
            await interaction.followup.send(
                "正式名称を登録できませんでした。時間をおいて再度お試しください。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"「{_escape_for_discord(canonical)}」を正式名称として登録しました。",
            ephemeral=True,
        )
        if interaction.message is not None:
            await interaction.message.edit(view=None)

    @discord.ui.button(
        label="入力名を別名として登録",
        style=discord.ButtonStyle.primary,
        custom_id="unverified_name:alias",
    )
    async def register_alias(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if not await self._require_pending_review(interaction):
            return
        await interaction.response.send_modal(
            CharacterAliasModal(self.review_id, db_path=self.db_path)
        )

    @discord.ui.button(
        label="無視",
        style=discord.ButtonStyle.secondary,
        custom_id="unverified_name:ignore",
    )
    async def ignore(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if not await self._require_pending_review(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            dismissed = dismiss_unverified_name_review(
                self.review_id,
                str(interaction.user.id),
                db_path=self.db_path,
            )
        except sqlite3.Error:
            logger.exception("未確認名の無視処理に失敗しました。")
            await interaction.followup.send(
                "未確認名を処理できませんでした。時間をおいて再度お試しください。",
                ephemeral=True,
            )
            return
        if not dismissed:
            await interaction.followup.send(
                "この未確認名はすでに処理されています。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "通知を無視しました。取引データは未確認のまま保存されています。",
            ephemeral=True,
        )
        if interaction.message is not None:
            await interaction.message.edit(view=None)


async def publish_unverified_name_notification(
    client: discord.Client,
    review: UnverifiedNameReview,
    db_path: str | Path = DATABASE_PATH,
) -> bool:
    """Send one review notice if its destination has been configured."""
    if review.status != "pending":
        return False
    channel_id = get_configured_unverified_name_channel(db_path)
    if channel_id is None:
        logger.warning(
            "未確認名の通知先が未設定です。/set_unverified_channel を実行してください。"
        )
        return False

    try:
        channel = client.get_channel(int(channel_id))
        if channel is None:
            channel = await client.fetch_channel(int(channel_id))
        send = getattr(channel, "send", None)
        if send is None:
            logger.error("設定された通知先はメッセージを送信できるチャンネルではありません。")
            return False
        message = await send(
            embed=format_unverified_name_notification(review),
            view=UnverifiedNameReviewView(review.id, db_path=db_path),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        record_unverified_name_notification(
            review.id,
            str(message.id),
            db_path=db_path,
        )
    except (discord.HTTPException, sqlite3.Error, ValueError):
        logger.exception("未確認名のDiscord通知に失敗しました。")
        return False
    return True


async def publish_pending_unverified_name_notifications(
    client: discord.Client,
    db_path: str | Path = DATABASE_PATH,
) -> int:
    """Publish queued names that have not yet received a Discord message."""
    try:
        reviews = get_pending_character_name_reviews(
            db_path,
            notification_missing=True,
        )
    except sqlite3.Error:
        logger.exception("未確認名通知の読み込みに失敗しました。")
        return 0

    sent = 0
    for review in reviews:
        if await publish_unverified_name_notification(client, review, db_path):
            sent += 1
    return sent


def format_worker_trade_notification(trade: ReviewTrade) -> discord.Embed:
    embed = discord.Embed(
        title=f"Web取引の審査 #{trade.trade_id}",
        description=(
            f"状態: 未審査\n"
            f"形式: {_escape_for_discord(trade.trade_type)}\n"
            f"取引価格: {trade.total_price:,}\n"
            f"受付日時: {_escape_for_discord(trade.submitted_at)[:50]}"
        ),
        color=discord.Color.gold(),
    )
    for item_index, item in enumerate(trade.items, start=1):
        character = item.character_name or item.character_id
        if item.character_number:
            character = f"{item.character_number} / {character}"
        embed.add_field(
            name=f"キャラクター {item_index}",
            value="\n".join(
                [
                    f"**{_escape_for_discord(character)[:100]}**",
                    f"レアリティ: {_escape_for_discord(item.rarity)[:40]}",
                    f"レベル: {item.level}",
                    f"変異: {_escape_for_discord(item.mutation)[:40]}",
                    f"個数: {item.quantity}",
                ]
            ),
            inline=False,
        )
    return embed


class WorkerTradeReviewView(discord.ui.View):
    """Persistent approval controls for a pending Worker trade."""

    def __init__(self, trade_id: int) -> None:
        super().__init__(timeout=None)
        self.trade_id = trade_id

    async def _decide(
        self,
        interaction: discord.Interaction,
        status: str,
    ) -> None:
        if not _can_review_worker_trades(interaction):
            await interaction.response.send_message(
                "この操作を実行する権限がありません。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        admin_key = os.environ.get(WORKER_ADMIN_KEY_ENV_VAR, "").strip()
        try:
            await asyncio.to_thread(
                update_trade_status,
                self.trade_id,
                status,
                reviewer_id=str(interaction.user.id),
                admin_key=admin_key,
            )
        except TradeReviewAPIError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            if "すでに審査" in str(error) and interaction.message is not None:
                await interaction.message.edit(view=None)
            return
        except (discord.HTTPException, OSError):
            logger.exception("Worker取引の審査操作に失敗しました。")
            await interaction.followup.send(
                "審査結果を保存できませんでした。時間をおいて再度お試しください。",
                ephemeral=True,
            )
            return

        result_label = "承認" if status == "approved" else "拒否"
        if interaction.message is not None:
            await interaction.message.edit(
                content=f"取引 #{self.trade_id} は{result_label}されました。",
                view=None,
            )
        await interaction.followup.send(
            f"取引 #{self.trade_id} を{result_label}しました。",
            ephemeral=True,
        )

    @discord.ui.button(
        label="承認",
        style=discord.ButtonStyle.success,
        custom_id="worker_trade_review:approve",
    )
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        await self._decide(interaction, "approved")

    @discord.ui.button(
        label="拒否",
        style=discord.ButtonStyle.danger,
        custom_id="worker_trade_review:reject",
    )
    async def reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        await self._decide(interaction, "rejected")


def format_worker_exchange_notification(exchange: ReviewExchange) -> discord.Embed:
    embed = discord.Embed(
        title=f"交換のみ取引の審査 #{exchange.exchange_id}",
        description=(
            "状態: 未審査\n"
            f"交換内容: {_escape_for_discord(exchange.exchange_description)[:1000]}\n"
            f"受付日時: {_escape_for_discord(exchange.submitted_at)[:50]}"
        ),
        color=discord.Color.blurple(),
    )
    for item_index, item in enumerate(exchange.items, start=1):
        character = item.character_name or item.character_id
        if item.character_number:
            character = f"{item.character_number} / {character}"
        embed.add_field(
            name=f"対象キャラクター {item_index}",
            value="\n".join(
                [
                    f"**{_escape_for_discord(character)[:100]}**",
                    f"レアリティ: {_escape_for_discord(item.rarity)[:40]}",
                    f"レベル: {item.level}",
                    f"変異: {_escape_for_discord(item.mutation)[:40]}",
                    f"個数: {item.quantity}",
                ]
            ),
            inline=False,
        )
    return embed


class WorkerExchangeReviewView(discord.ui.View):
    """Persistent approval controls for a pending exchange submission."""

    def __init__(self, exchange_id: int) -> None:
        super().__init__(timeout=None)
        self.exchange_id = exchange_id

    async def _decide(
        self,
        interaction: discord.Interaction,
        status: str,
    ) -> None:
        if not _can_review_worker_trades(interaction):
            await interaction.response.send_message(
                "この操作を実行する権限がありません。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        admin_key = os.environ.get(WORKER_ADMIN_KEY_ENV_VAR, "").strip()
        try:
            await asyncio.to_thread(
                update_exchange_status,
                self.exchange_id,
                status,
                reviewer_id=str(interaction.user.id),
                admin_key=admin_key,
            )
        except TradeReviewAPIError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            if "すでに審査" in str(error) and interaction.message is not None:
                await interaction.message.edit(view=None)
            return
        except (discord.HTTPException, OSError):
            logger.exception("交換のみ取引の審査操作に失敗しました。")
            await interaction.followup.send(
                "審査結果を保存できませんでした。時間をおいて再度お試しください。",
                ephemeral=True,
            )
            return

        result_label = "承認" if status == "approved" else "拒否"
        if interaction.message is not None:
            await interaction.message.edit(
                content=f"交換のみ取引 #{self.exchange_id} は{result_label}されました。",
                view=None,
            )
        await interaction.followup.send(
            f"交換のみ取引 #{self.exchange_id} を{result_label}しました。",
            ephemeral=True,
        )

    @discord.ui.button(
        label="承認",
        style=discord.ButtonStyle.success,
        custom_id="worker_exchange_review:approve",
    )
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        await self._decide(interaction, "approved")

    @discord.ui.button(
        label="拒否",
        style=discord.ButtonStyle.danger,
        custom_id="worker_exchange_review:reject",
    )
    async def reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        await self._decide(interaction, "rejected")


async def publish_pending_worker_trade_notifications(
    client: discord.Client,
    db_path: str | Path = DATABASE_PATH,
) -> int:
    """Send new Worker submissions to the configured review channel once."""
    channel_id = get_worker_trade_review_channel(db_path)
    admin_key = os.environ.get(WORKER_ADMIN_KEY_ENV_VAR, "").strip()
    if channel_id is None:
        logger.warning(
            "Web取引の審査先が未設定です。/set_trade_review_channel を実行してください。"
        )
        return 0
    if not admin_key:
        logger.error("%s がBot環境に設定されていません。", WORKER_ADMIN_KEY_ENV_VAR)
        return 0

    try:
        pending = await asyncio.to_thread(get_pending_trades, admin_key=admin_key)
        notified = {trade_id for trade_id, _ in get_worker_trade_notification_ids(db_path)}
        channel = client.get_channel(int(channel_id))
        if channel is None:
            channel = await client.fetch_channel(int(channel_id))
        send = getattr(channel, "send", None)
        if send is None:
            logger.error("設定された審査先はメッセージを送信できるチャンネルではありません。")
            return 0
        sent = 0
        for trade in pending:
            if trade.trade_id in notified:
                continue
            message = await send(
                embed=format_worker_trade_notification(trade),
                view=WorkerTradeReviewView(trade.trade_id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            record_worker_trade_notification(
                trade.trade_id,
                channel_id,
                str(message.id),
                db_path,
            )
            sent += 1
        return sent
    except (TradeReviewAPIError, discord.HTTPException, sqlite3.Error, ValueError):
        logger.exception("Web取引の審査通知を送信できませんでした。")
        return 0


async def publish_pending_worker_exchange_notifications(
    client: discord.Client,
    db_path: str | Path = DATABASE_PATH,
) -> int:
    channel_id = get_worker_trade_review_channel(db_path)
    admin_key = os.environ.get(WORKER_ADMIN_KEY_ENV_VAR, "").strip()
    if channel_id is None or not admin_key:
        return 0

    try:
        pending = await asyncio.to_thread(get_pending_exchanges, admin_key=admin_key)
        notified = {
            exchange_id
            for exchange_id, _ in get_worker_exchange_notification_ids(db_path)
        }
        channel = client.get_channel(int(channel_id))
        if channel is None:
            channel = await client.fetch_channel(int(channel_id))
        send = getattr(channel, "send", None)
        if send is None:
            logger.error("設定された審査先はメッセージを送信できるチャンネルではありません。")
            return 0
        sent = 0
        for exchange in pending:
            if exchange.exchange_id in notified:
                continue
            message = await send(
                embed=format_worker_exchange_notification(exchange),
                view=WorkerExchangeReviewView(exchange.exchange_id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            record_worker_exchange_notification(
                exchange.exchange_id,
                channel_id,
                str(message.id),
                db_path,
            )
            sent += 1
        return sent
    except (TradeReviewAPIError, discord.HTTPException, sqlite3.Error, ValueError):
        logger.exception("交換のみ取引の審査通知を送信できませんでした。")
        return 0


async def _poll_worker_trade_reviews(client: discord.Client) -> None:
    while not client.is_closed():
        await publish_pending_worker_trade_notifications(client)
        await publish_pending_worker_exchange_notifications(client)
        await asyncio.sleep(120)


class SingleTradeModal(discord.ui.Modal, title="単体・まとめ買い"):
    """Modal for collecting a single-character trade."""
    character_name = discord.ui.TextInput(
        label="キャラ名",
        placeholder="例: Trade Value",
        max_length=100,
    )
    mutation = discord.ui.TextInput(
        label="変異",
        placeholder="例: 通常、金、虹",
        max_length=100,
    )
    level = discord.ui.TextInput(
        label="レベル",
        placeholder="例: 50",
        max_length=30,
    )
    quantity = discord.ui.TextInput(
        label="個数",
        placeholder="例: 1 または 3",
        max_length=30,
    )
    total_amount = discord.ui.TextInput(
        label="合計金額",
        placeholder="例: 1.5k",
        max_length=50,
    )

    def __init__(self, db_path: str | Path = DATABASE_PATH) -> None:
        super().__init__()
        self.db_path = Path(db_path)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            quantity = parse_quantity(str(self.quantity.value))
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        character_name = str(self.character_name.value).strip()
        mutation = str(self.mutation.value).strip()
        level = str(self.level.value).strip()
        total_amount = str(self.total_amount.value).strip()
        transaction_type = transaction_type_for_quantity(quantity)
        try:
            save_trade(
                character_name=character_name,
                mutation=mutation,
                quantity=quantity,
                total_amount=total_amount,
                transaction_type=transaction_type,
                registered_by_discord_user_id=str(interaction.user.id),
                db_path=self.db_path,
                level=level,
            )
        except sqlite3.Error:
            logger.exception("取引の保存に失敗しました。")
            await interaction.response.send_message(
                "取引の保存に失敗しました。時間をおいて再度お試しください。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "取引を保存しました。\n\n"
            + format_trade_confirmation(
                character_name,
                mutation,
                str(quantity),
                total_amount,
                transaction_type,
                level,
            ),
            ephemeral=True,
        )


class SetCharacterModal(discord.ui.Modal, title="セットのキャラ入力"):
    """Modal for collecting one character in a set transaction."""

    character_name = discord.ui.TextInput(
        label="キャラ名",
        placeholder="例: Trade Value",
        max_length=100,
    )
    mutation = discord.ui.TextInput(
        label="変異",
        placeholder="例: 通常、金、虹",
        max_length=100,
    )
    level = discord.ui.TextInput(
        label="レベル",
        placeholder="例: 50",
        max_length=30,
    )
    quantity = discord.ui.TextInput(
        label="個数",
        placeholder="例: 1",
        max_length=30,
    )

    def __init__(
        self,
        characters: Sequence[TradeCharacter] = (),
        db_path: str | Path = DATABASE_PATH,
    ) -> None:
        super().__init__()
        self.characters = tuple(characters)
        self.db_path = Path(db_path)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            quantity = parse_quantity(str(self.quantity.value))
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        character = TradeCharacter(
            character_name=str(self.character_name.value).strip(),
            mutation=str(self.mutation.value).strip(),
            level=str(self.level.value).strip(),
            quantity=quantity,
        )
        characters = self.characters + (character,)
        await interaction.response.send_message(
            f"{len(characters)}体目を入力しました。"
            "続けてキャラを追加するか、セット全体の合計金額を入力してください。",
            view=SetTradeOptionsView(characters, db_path=self.db_path),
            ephemeral=True,
        )


class SetTotalAmountModal(discord.ui.Modal, title="セット合計金額"):
    """Modal for collecting the total price for the entire set."""

    total_amount = discord.ui.TextInput(
        label="セット全体の合計金額",
        placeholder="例: 1.5k",
        max_length=50,
    )

    def __init__(
        self,
        characters: Sequence[TradeCharacter],
        db_path: str | Path = DATABASE_PATH,
    ) -> None:
        super().__init__()
        self.characters = tuple(characters)
        self.db_path = Path(db_path)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        total_amount = str(self.total_amount.value).strip()
        try:
            save_set_trade(
                characters=self.characters,
                total_amount=total_amount,
                registered_by_discord_user_id=str(interaction.user.id),
                db_path=self.db_path,
            )
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        except sqlite3.Error:
            logger.exception("セット取引の保存に失敗しました。")
            await interaction.response.send_message(
                "セット取引の保存に失敗しました。時間をおいて再度お試しください。",
                ephemeral=True,
            )
            return

        confirmation = ["セット取引を保存しました。"]
        for index, character in enumerate(self.characters, start=1):
            confirmation.append(
                f"**キャラ{index}**: "
                f"{_escape_for_discord(character.character_name)} / "
                f"{_escape_for_discord(character.mutation)} / "
                f"レベル {_escape_for_discord(character.level)} / "
                f"個数 {_escape_for_discord(str(character.quantity))}"
            )
        confirmation.append(
            f"**セット全体の合計金額**: {_escape_for_discord(total_amount)}"
        )
        await interaction.response.send_message(
            "\n".join(confirmation),
            ephemeral=True,
        )


class SetTradeOptionsView(discord.ui.View):
    """Offer another character or move to the set's total price."""

    def __init__(
        self,
        characters: Sequence[TradeCharacter],
        db_path: str | Path = DATABASE_PATH,
    ) -> None:
        super().__init__(timeout=300)
        self.characters = tuple(characters)
        self.db_path = Path(db_path)
        self._choice_consumed = False
        if len(self.characters) >= 3:
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.label == "キャラを追加":
                    item.disabled = True

    @discord.ui.button(
        label="キャラを追加",
        style=discord.ButtonStyle.secondary,
    )
    async def add_character(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if self._choice_consumed:
            await interaction.response.send_message(
                "この入力画面はすでに使用されています。新しく取引を開始してください。",
                ephemeral=True,
            )
            return
        if len(self.characters) >= 3:
            await interaction.response.send_message(
                "セットに登録できるキャラは最大3体です。",
                ephemeral=True,
            )
            return
        self._choice_consumed = True
        self.stop()
        await interaction.response.send_modal(
            SetCharacterModal(self.characters, db_path=self.db_path)
        )

    @discord.ui.button(
        label="セット合計金額を入力",
        style=discord.ButtonStyle.primary,
    )
    async def enter_total_amount(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if self._choice_consumed:
            await interaction.response.send_message(
                "この入力画面はすでに使用されています。新しく取引を開始してください。",
                ephemeral=True,
            )
            return
        self._choice_consumed = True
        self.stop()
        await interaction.response.send_modal(
            SetTotalAmountModal(self.characters, db_path=self.db_path)
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[discord.ui.View],
    ) -> None:
        logger.error("セット取引の選択処理に失敗しました。", exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "セット取引の入力を続けられませんでした。最初からやり直してください。",
                ephemeral=True,
            )


class TradeTypeView(discord.ui.View):
    """Offer the two trade entry paths after the entry button is pressed."""

    def __init__(self, db_path: str | Path = DATABASE_PATH) -> None:
        super().__init__(timeout=300)
        self.db_path = Path(db_path)
        self._choice_consumed = False

    @discord.ui.button(
        label="単体・まとめ買い",
        style=discord.ButtonStyle.primary,
    )
    async def single_or_bulk(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if self._choice_consumed:
            await interaction.response.send_message(
                "この入力画面はすでに使用されています。新しく取引を開始してください。",
                ephemeral=True,
            )
            return
        self._choice_consumed = True
        self.stop()
        await interaction.response.send_modal(
            SingleTradeModal(db_path=self.db_path)
        )

    @discord.ui.button(
        label="セット",
        style=discord.ButtonStyle.secondary,
    )
    async def set_trade(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if self._choice_consumed:
            await interaction.response.send_message(
                "この入力画面はすでに使用されています。新しく取引を開始してください。",
                ephemeral=True,
            )
            return
        self._choice_consumed = True
        self.stop()
        await interaction.response.send_modal(
            SetCharacterModal(db_path=self.db_path)
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[discord.ui.View],
    ) -> None:
        logger.error("取引タイプの選択に失敗しました。", exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "取引タイプを選択できませんでした。もう一度お試しください。",
                ephemeral=True,
            )


class TradeEntryView(discord.ui.View):
    """Persistent view containing the trade entry button."""

    def __init__(self, db_path: str | Path = DATABASE_PATH) -> None:
        super().__init__(timeout=None)
        self.db_path = Path(db_path)

    @discord.ui.button(
        label="取引記入",
        style=discord.ButtonStyle.primary,
        custom_id="trade_entry:open_modal",
    )
    async def open_modal(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        await interaction.response.send_message(
            "取引の種類を選択してください。",
            view=TradeTypeView(db_path=self.db_path),
            ephemeral=True,
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[discord.ui.View],
    ) -> None:
        logger.error("取引記入ボタンの処理に失敗しました。", exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "取引記入フォームを開けませんでした。時間をおいて再度お試しください。",
                ephemeral=True,
            )


class PythonAppBot(discord.Client):
    """Bot implementation for the Python App starter."""

    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.add_view(TradeEntryView())
        self.worker_trade_poll_task: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        """Sync global slash commands; remove stale guild-scoped duplicates on ready."""
        for trade_id, message_id in get_worker_trade_notification_ids():
            self.add_view(
                WorkerTradeReviewView(trade_id),
                message_id=int(message_id),
            )
        for exchange_id, message_id in get_worker_exchange_notification_ids():
            self.add_view(
                WorkerExchangeReviewView(exchange_id),
                message_id=int(message_id),
            )
        synced_commands = await self.tree.sync()
        command_names = ", ".join(command.name for command in synced_commands)
        logger.info(
            "スラッシュコマンドを同期しました (%d件): %s",
            len(synced_commands),
            command_names,
        )

    async def close(self) -> None:
        if self.worker_trade_poll_task is not None:
            self.worker_trade_poll_task.cancel()
            try:
                await self.worker_trade_poll_task
            except asyncio.CancelledError:
                pass
        await super().close()



def get_discord_token(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the Discord token without exposing it in logs or errors."""
    environment = os.environ if environ is None else environ
    token = environment.get(TOKEN_ENV_VAR, "").strip()

    if not token:
        raise RuntimeError(
            f"{TOKEN_ENV_VAR} is not configured. "
            "Add it to Replit Secrets before starting the bot."
        )

    return token


def create_bot() -> PythonAppBot:
    """Create the bot with only the intents needed by its slash commands."""
    bot = PythonAppBot()

    @bot.event
    async def on_ready() -> None:
        if bot.user is None:
            logger.info("Discordに接続しました。")
            return

        logger.info(
            "Discordに接続しました: %s (ID: %s)",
            bot.user.name,
            bot.user.id,
        )
        if (
            bot.worker_trade_poll_task is None
            or bot.worker_trade_poll_task.done()
        ):
            bot.worker_trade_poll_task = asyncio.create_task(
                _poll_worker_trade_reviews(bot)
            )

        # Remove old guild-scoped copies so each slash command appears only
        # once. The canonical commands are synced globally in setup_hook.
        for guild in bot.guilds:
            try:
                bot.tree.clear_commands(guild=guild)
                await bot.tree.sync(guild=guild)
                logger.info(
                    "重複したギルドコマンドを整理しました: %s",
                    guild.name,
                )
            except discord.HTTPException:
                logger.exception(
                    "ギルドコマンドの整理に失敗しました: %s", guild.id
                )

    @bot.tree.command(name="ping", description="Check whether the bot is online.")
    async def ping(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Pong!")

    @bot.tree.command(
        name="trade",
        description="取引記入ボタンを表示します。",
    )
    async def trade(interaction: discord.Interaction) -> None:
        logger.info("/tradeを受信しました。")
        await interaction.response.send_message(
            "取引内容を入力する場合は、下のボタンを押してください。",
            view=TradeEntryView(),
        )

    MARKET_RARITIES = (
        ("common", "Common / コモン"),
        ("rare", "Rare / レア"),
        ("epic", "Epic / エピック"),
        ("legend", "Legend / レジェンド"),
        ("mythic", "Mythic / ミシック"),
        ("secret", "Secret / シークレット"),
        ("ultimate", "Ultimate / アルティメット"),
        ("emperor", "Emperor / エンペラー"),
    )

    def format_market_snapshot(snapshot: object) -> str:
        def price_text(value: float | None) -> str:
            return "データ不足" if value is None else f"{value:,.0f}"

        lines = [
            (
                f"**{snapshot.character_number} / "
                f"{_escape_for_discord(snapshot.character_name)} の市場スナップショット**"
                if snapshot.character_number
                else f"**{_escape_for_discord(snapshot.character_name)} の市場スナップショット**"
            ),
            f"Lv.1価格: **{price_text(snapshot.level1_value)}** {snapshot.level1_direction}",
            (
                f"Lv.1サンプル数: {snapshot.level1_sample_count}件"
                if snapshot.level1_sample_count is not None
                else "Lv.1サンプル数: 過去データでは未記録"
            ),
            f"Lv.MAX価格: **{price_text(snapshot.level_max_value)}** {snapshot.level_max_direction}",
            (
                f"Lv.MAXサンプル数: {snapshot.level_max_sample_count}件"
                if snapshot.level_max_sample_count is not None
                else "Lv.MAXサンプル数: 過去データでは未記録"
            ),
            (
                f"需要度: **{snapshot.demand_score:.1f}/5.0** {snapshot.demand_direction}"
                if snapshot.demand_score is not None
                else f"需要度: **データ不足** {snapshot.demand_direction}"
            ),
            "",
            "**変異倍率**",
        ]
        if snapshot.acquired_count is None or snapshot.exchange_acquired_count is None or snapshot.given_count is None:
            lines.append("需要内訳: 過去データでは未記録")
        else:
            lines.extend([
                f"入手件数: {snapshot.acquired_count}件",
                f"交換での入手件数: {snapshot.exchange_acquired_count}件",
                f"手放した件数: {snapshot.given_count}件",
                (
                    f"入手需要の前回更新との差: {snapshot.demand_delta:+d}件"
                    if snapshot.demand_delta is not None
                    else "入手需要の前回更新との差: 未記録"
                ),
            ])
        mutation_labels = {
            "normal": "ノーマル", "ノーマル": "ノーマル",
            "silver": "シルバー", "シルバー": "シルバー",
            "gold": "ゴールド", "ゴールド": "ゴールド",
            "diamond": "ダイヤ", "ダイヤ": "ダイヤ",
            "lava": "ラバ", "ラバ": "ラバ",
            "galaxy": "ギャラ", "ギャラ": "ギャラ",
            "aqua": "アクア", "アクア": "アクア",
            "yggdrasil": "ユグ", "ユグ": "ユグ",
            "halo": "ハロ", "ハロ": "ハロ",
            "halloween": "ハロウィン", "ハロウィン": "ハロウィン",
        }
        for mutation in snapshot.mutations:
            label = mutation_labels.get(mutation.mutation, mutation.mutation)
            multiplier_text = (
                "データ不足" if mutation.multiplier is None
                else f"{mutation.multiplier:.2f}x"
            )
            lines.append(
                f"{_escape_for_discord(label)}: **{multiplier_text}** {mutation.direction}"
            )
        lines.extend([
            "",
            f"需要対象取引: {snapshot.sample_count}件",
            f"更新日時: {snapshot.created_at}",
            "※ ↑上昇 / ↓下落 / →変化なし（または比較データなし）",
        ])
        return "\n".join(lines)

    class MarketCharacterSelect(discord.ui.Select):
        def __init__(self, parent_view: "MarketCharacterView") -> None:
            self.parent_view = parent_view
            start_index = parent_view.page * 25
            page_characters = parent_view.characters[start_index:start_index + 25]
            options = [
                discord.SelectOption(
                    label=f"{item.character_number} · {item.english_name}"[:100],
                    value=item.character_number,
                    description=f"{item.japanese_name} / {item.rarity}"[:100],
                )
                for item in page_characters
            ]
            super().__init__(
                placeholder="キャラクターを選択",
                min_values=1,
                max_values=1,
                options=options,
            )

        async def callback(self, interaction: discord.Interaction) -> None:
            await self.parent_view.choose_character(interaction, self.values[0])

    class MarketCharacterView(discord.ui.View):
        def __init__(
            self,
            owner_id: int,
            characters: list[CharacterCatalogEntry],
            rarity: str,
        ) -> None:
            super().__init__(timeout=180)
            self.owner_id = owner_id
            self.characters = [item for item in characters if item.rarity == rarity]
            self.rarity = rarity
            self.page = 0
            self.refresh_items()

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "この選択メニューを開いた人だけ操作できます。", ephemeral=True
                )
                return False
            return True

        def refresh_items(self) -> None:
            self.clear_items()
            self.add_item(MarketCharacterSelect(self))
            if len(self.characters) > 25:
                previous = discord.ui.Button(
                    label="前へ", style=discord.ButtonStyle.secondary,
                    disabled=self.page == 0,
                )
                next_button = discord.ui.Button(
                    label="次へ", style=discord.ButtonStyle.secondary,
                    disabled=(self.page + 1) * 25 >= len(self.characters),
                )
                previous.callback = self.go_previous
                next_button.callback = self.go_next
                self.add_item(previous)
                self.add_item(next_button)

        async def go_previous(self, interaction: discord.Interaction) -> None:
            self.page = max(0, self.page - 1)
            self.refresh_items()
            await interaction.response.edit_message(content=self.prompt_text(), view=self)

        async def go_next(self, interaction: discord.Interaction) -> None:
            self.page = min((len(self.characters) - 1) // 25, self.page + 1)
            self.refresh_items()
            await interaction.response.edit_message(content=self.prompt_text(), view=self)

        def prompt_text(self) -> str:
            rarity_label = dict(MARKET_RARITIES).get(self.rarity, self.rarity)
            page_count = max(1, (len(self.characters) + 24) // 25)
            return (
                f"レアリティ: **{rarity_label}** — キャラを選択してください。"
                f"（{self.page + 1}/{page_count}ページ）"
            )

        async def choose_character(
            self, interaction: discord.Interaction, character_number: str
        ) -> None:
            await interaction.response.edit_message(
                content="相場データを取得しています…", view=None
            )
            try:
                snapshot = await asyncio.to_thread(
                    get_market_snapshot, None, character_number=character_number
                )
            except MarketAPIError as error:
                await interaction.followup.send(
                    f"市場データを取得できませんでした。\n{error}", ephemeral=True
                )
                return
            await interaction.followup.send(
                format_market_snapshot(snapshot), ephemeral=True
            )

    class MarketRaritySelect(discord.ui.Select):
        def __init__(
            self, owner_id: int, characters: list[CharacterCatalogEntry]
        ) -> None:
            self.owner_id = owner_id
            self.characters = characters
            options = [
                discord.SelectOption(label=label, value=value)
                for value, label in MARKET_RARITIES
                if any(item.rarity == value for item in characters)
            ]
            super().__init__(
                placeholder="最初にレアリティを選択",
                min_values=1,
                max_values=1,
                options=options,
            )

        async def callback(self, interaction: discord.Interaction) -> None:
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "この選択メニューを開いた人だけ操作できます。", ephemeral=True
                )
                return
            rarity = self.values[0]
            view = MarketCharacterView(self.owner_id, self.characters, rarity)
            if not view.characters:
                await interaction.response.edit_message(
                    content="このレアリティのキャラは登録されていません。", view=None
                )
                return
            await interaction.response.edit_message(
                content=view.prompt_text(), view=view
            )

    class MarketRarityView(discord.ui.View):
        def __init__(
            self, owner_id: int, characters: list[CharacterCatalogEntry]
        ) -> None:
            super().__init__(timeout=180)
            self.add_item(MarketRaritySelect(owner_id, characters))

    @bot.tree.command(
        name="market",
        description="レアリティとキャラを選んで相場情報を表示します。",
    )
    async def market(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            characters = await asyncio.to_thread(get_characters)
        except MarketAPIError as error:
            await interaction.followup.send(
                f"キャラ一覧を取得できませんでした。\n{error}", ephemeral=True
            )
            return
        if not characters:
            await interaction.followup.send("Workerのキャラ一覧が空です。", ephemeral=True)
            return
        await interaction.followup.send(
            "まずレアリティを選択してください。",
            view=MarketRarityView(interaction.user.id, characters),
            ephemeral=True,
        )

    @bot.tree.command(
        name="trades",
        description="最新20件の取引データを表示します。",
    )
    async def trades(interaction: discord.Interaction) -> None:
        try:
            recent_trades = get_recent_trades()
        except sqlite3.Error:
            logger.exception("取引データの読み込みに失敗しました。")
            await interaction.response.send_message(
                "取引データを読み込めませんでした。時間をおいて再度お試しください。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=format_recent_trades(recent_trades),
            ephemeral=True,
        )

    @bot.tree.error
    async def on_app_command_error(
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        logger.error("スラッシュコマンドの処理に失敗しました。", exc_info=True)
        message = (
            "コマンドの実行中にエラーが発生しました。"
            "Botの権限を確認して、もう一度お試しください。"
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    return bot


def run_bot(token: str | None = None) -> None:
    """Start the Discord client using the configured token."""
    initialize_database()
    resolved_token = token if token is not None else get_discord_token()
    bot = create_bot()
    bot.run(resolved_token)


def main() -> None:
    """Start the bot process with a safe, user-facing configuration error."""
    logging.basicConfig(level=logging.INFO)

    try:
        run_bot()
    except RuntimeError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
