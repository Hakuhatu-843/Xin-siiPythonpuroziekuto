"""A minimal Discord bot that reads its token from Replit Secrets."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import discord
from discord import app_commands

TOKEN_ENV_VAR = "DISCORD_TOKEN"
TRANSACTION_TYPES = ("通常", "まとめ買い", "セット")
DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "trades.sqlite3"
logger = logging.getLogger(__name__)


def initialize_database(db_path: str | Path = DATABASE_PATH) -> None:
    """Create the SQLite database and trades table when they are missing."""
    database_path = Path(db_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_name TEXT NOT NULL,
                mutation TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                total_amount TEXT NOT NULL,
                transaction_type TEXT NOT NULL
                    CHECK (transaction_type IN ('通常', 'まとめ買い', 'セット')),
                registered_at TEXT NOT NULL,
                registered_by_discord_user_id TEXT NOT NULL
            )
            """
        )


def parse_quantity(value: str) -> int:
    """Convert the quantity field to a positive integer."""
    try:
        quantity = int(value.strip())
    except ValueError as error:
        raise ValueError("個数は1以上の整数で入力してください。") from error

    if quantity < 1:
        raise ValueError("個数は1以上の整数で入力してください。")

    return quantity


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
) -> int:
    """Persist one trade entry and return its database ID."""
    initialize_database(db_path)
    timestamp = registered_at or datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO trades (
                character_name,
                mutation,
                quantity,
                total_amount,
                transaction_type,
                registered_at,
                registered_by_discord_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                character_name,
                mutation,
                quantity,
                total_amount,
                transaction_type,
                timestamp,
                registered_by_discord_user_id,
            ),
        )

    return int(cursor.lastrowid)


def _escape_for_discord(value: str) -> str:
    """Prevent user-entered values from creating mentions or markdown."""
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))


def format_trade_confirmation(
    character_name: str,
    mutation: str,
    quantity: str,
    total_amount: str,
    transaction_type: str,
) -> str:
    """Format a trade entry for confirmation without persisting it."""
    return "\n".join(
        [
            "取引内容を確認してください。",
            f"**キャラ名**: {_escape_for_discord(character_name)}",
            f"**変異**: {_escape_for_discord(mutation)}",
            f"**個数**: {_escape_for_discord(quantity)}",
            f"**合計金額**: {_escape_for_discord(total_amount)}",
            f"**取引タイプ**: {_escape_for_discord(transaction_type)}",
        ]
    )


class TradeEntryModal(discord.ui.Modal, title="取引記入"):
    """Modal for collecting one trade entry."""

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
    quantity = discord.ui.TextInput(
        label="個数",
        placeholder="例: 3",
        max_length=30,
    )
    total_amount = discord.ui.TextInput(
        label="合計金額",
        placeholder="例: 1500円",
        max_length=50,
    )
    transaction_type = discord.ui.TextInput(
        label="取引タイプ",
        placeholder="通常 / まとめ買い / セット",
        max_length=20,
    )

    def __init__(self, db_path: str | Path = DATABASE_PATH) -> None:
        super().__init__()
        self.db_path = Path(db_path)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        transaction_type = str(self.transaction_type.value).strip()

        if transaction_type not in TRANSACTION_TYPES:
            await interaction.response.send_message(
                "取引タイプは「通常」「まとめ買い」「セット」のいずれかを入力してください。",
                ephemeral=True,
            )
            return

        try:
            quantity = parse_quantity(str(self.quantity.value))
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        try:
            save_trade(
                character_name=str(self.character_name.value).strip(),
                mutation=str(self.mutation.value).strip(),
                quantity=quantity,
                total_amount=str(self.total_amount.value).strip(),
                transaction_type=transaction_type,
                registered_by_discord_user_id=str(interaction.user.id),
                db_path=self.db_path,
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
                str(self.character_name.value).strip(),
                str(self.mutation.value).strip(),
                str(quantity),
                str(self.total_amount.value).strip(),
                transaction_type,
            ),
            ephemeral=True,
        )


class TradeEntryView(discord.ui.View):
    """Persistent view containing the trade entry button."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

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
        await interaction.response.send_modal(TradeEntryModal())


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
        await interaction.response.send_message(
            "取引内容を入力する場合は、下のボタンを押してください。",
            view=TradeEntryView(),
        )

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
