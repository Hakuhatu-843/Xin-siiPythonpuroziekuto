"""A minimal Discord bot that reads its token from Replit Secrets."""

from __future__ import annotations

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

TOKEN_ENV_VAR = "DISCORD_TOKEN"
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
                UNIQUE (trade_id, position)
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
            level
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )
    trade_id = int(cursor.lastrowid)
    connection.executemany(
        """
        INSERT INTO trade_characters (
            trade_id, position, character_name, mutation, level, quantity
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                trade_id,
                position,
                character.character_name,
                character.mutation,
                character.level,
                character.quantity,
            )
            for position, character in enumerate(characters, start=1)
        ],
    )
    return trade_id


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
    initialize_database(db_path)
    timestamp = registered_at or datetime.now(timezone.utc).isoformat()
    character = TradeCharacter(
        character_name=character_name,
        mutation=mutation,
        level=level,
        quantity=quantity,
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        trade_id = _insert_trade(
            connection,
            character_name=character_name,
            mutation=mutation,
            quantity=quantity,
            total_amount=total_amount,
            transaction_type=transaction_type,
            registered_by_discord_user_id=registered_by_discord_user_id,
            registered_at=timestamp,
            level=level,
            characters=(character,),
        )

    return trade_id


def save_set_trade(
    *,
    characters: Sequence[TradeCharacter],
    total_amount: str,
    registered_by_discord_user_id: str,
    db_path: str | Path = DATABASE_PATH,
    registered_at: str | None = None,
) -> int:
    """Save a set transaction and its character details without unit prices."""
    if not 1 <= len(characters) <= 3:
        raise ValueError("セットに登録できるキャラは1〜3体です。")
    if any(character.quantity < 1 for character in characters):
        raise ValueError("個数は1以上の整数で入力してください。")

    initialize_database(db_path)
    timestamp = registered_at or datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        return _insert_trade(
            connection,
            character_name=f"セット取引（{len(characters)}キャラ）",
            mutation="—",
            quantity=sum(character.quantity for character in characters),
            total_amount=total_amount,
            transaction_type="セット",
            registered_by_discord_user_id=registered_by_discord_user_id,
            registered_at=timestamp,
            level="",
            characters=characters,
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
                SELECT character_name, mutation, level, quantity
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
            prefix = f"キャラ{character_index}" if transaction_type == "セット" else "キャラ名"
            details = f"{prefix}: {name} / 変異: {mutation}"
            if level:
                details += f" / レベル: {level}"
            details += f" / 個数: {quantity}"
            character_lines.append(details)

        value = "\n".join(
            [
                *character_lines,
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

    async def setup_hook(self) -> None:
        """Sync slash commands once when the bot starts."""
        await self.tree.sync()


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
