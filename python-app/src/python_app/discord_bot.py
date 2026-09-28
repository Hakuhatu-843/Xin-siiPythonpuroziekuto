"""A minimal Discord bot that reads its token from Replit Secrets."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

import discord
from discord import app_commands

TOKEN_ENV_VAR = "DISCORD_TOKEN"
logger = logging.getLogger(__name__)


class PythonAppBot(discord.Client):
    """Bot implementation for the Python App starter."""

    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

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

    return bot


def run_bot(token: str | None = None) -> None:
    """Start the Discord client using the configured token."""
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
