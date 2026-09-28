"""A minimal Discord bot that reads its token from Replit Secrets."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

import discord
from discord.ext import commands

TOKEN_ENV_VAR = "DISCORD_TOKEN"


class PythonAppBot(commands.Bot):
    """Bot implementation for the Python App starter."""

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


def create_bot() -> commands.Bot:
    """Create the bot with only the intents needed by its slash commands."""
    intents = discord.Intents.default()
    bot = PythonAppBot(command_prefix="!", intents=intents)

    @bot.tree.command(name="ping", description="Check whether the bot is online.")
    async def ping(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Pong!")

    return bot


def run_bot(token: str | None = None) -> None:
    """Start the Discord client using the configured token."""
    bot = create_bot()
    bot.run(token if token is not None else get_discord_token())


def main() -> None:
    """Start the bot process with a safe, user-facing configuration error."""
    logging.basicConfig(level=logging.INFO)

    try:
        run_bot()
    except RuntimeError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
