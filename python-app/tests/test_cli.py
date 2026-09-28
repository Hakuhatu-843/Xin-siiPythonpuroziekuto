import json
import sqlite3
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from python_app.cli import build_greeting, main
from python_app.discord_bot import (
    TRANSACTION_TYPES,
    TradeEntryModal,
    TradeEntryView,
    create_bot,
    format_trade_confirmation,
    get_discord_token,
    initialize_database,
    parse_quantity,
    save_trade,
)


class GreetingTests(unittest.TestCase):
    def test_build_greeting_uses_name(self) -> None:
        self.assertEqual(build_greeting("Hana"), "Hello, Hana!")

    def test_build_greeting_defaults_for_blank_name(self) -> None:
        self.assertEqual(build_greeting("  "), "Hello, world!")

    def test_main_can_output_json(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            main(["--name", "Hana", "--json"])

        self.assertEqual(json.loads(output.getvalue()), {"greeting": "Hello, Hana!"})


class DiscordBotTests(unittest.TestCase):
    def test_get_discord_token_reads_only_the_named_environment_variable(self) -> None:
        self.assertEqual(
            get_discord_token({"DISCORD_TOKEN": "test-token"}),
            "test-token",
        )

    def test_get_discord_token_rejects_a_missing_secret(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "DISCORD_TOKEN"):
            get_discord_token({})

    def test_create_bot_registers_a_slash_ping_command_without_message_content_intent(
        self,
    ) -> None:
        bot = create_bot()

        self.assertIsNotNone(bot.tree.get_command("ping"))
        self.assertIsNotNone(bot.tree.get_command("trade"))
        self.assertFalse(bot.intents.message_content)

    def test_trade_view_has_a_persistent_button(self) -> None:
        view = TradeEntryView()

        self.assertIsNone(view.timeout)
        self.assertEqual(len(view.children), 1)
        self.assertEqual(view.children[0].label, "取引記入")

    def test_trade_modal_has_five_fields(self) -> None:
        modal = TradeEntryModal()

        self.assertEqual(modal.title, "取引記入")
        self.assertEqual(len(modal.children), 5)
        self.assertEqual(
            [child.label for child in modal.children],
            ["キャラ名", "変異", "個数", "合計金額", "取引タイプ"],
        )

    def test_trade_confirmation_escapes_user_input(self) -> None:
        confirmation = format_trade_confirmation(
            "A @everyone",
            "**金**",
            "2",
            "1,000円",
            TRANSACTION_TYPES[0],
        )

        self.assertIn("A @", confirmation)
        self.assertNotIn("@everyone", confirmation)
        self.assertIn("\\*\\*金\\*\\*", confirmation)
        self.assertIn("通常", confirmation)

    def test_initialize_database_creates_the_trades_table(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "nested" / "trades.sqlite3"

            initialize_database(database_path)

            with sqlite3.connect(database_path) as connection:
                table = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'trades'"
                ).fetchone()

            self.assertEqual(table, ("trades",))

    def test_save_trade_persists_all_requested_fields(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "trades.sqlite3"

            trade_id = save_trade(
                character_name="Trade Value",
                mutation="金",
                quantity=3,
                total_amount="1500円",
                transaction_type="まとめ買い",
                registered_by_discord_user_id="123456789",
                registered_at="2026-09-28T00:00:00+00:00",
                db_path=database_path,
            )

            with sqlite3.connect(database_path) as connection:
                row = connection.execute(
                    """
                    SELECT character_name, mutation, quantity, total_amount,
                           transaction_type, registered_at,
                           registered_by_discord_user_id
                    FROM trades
                    WHERE id = ?
                    """,
                    (trade_id,),
                ).fetchone()

            self.assertEqual(
                row,
                (
                    "Trade Value",
                    "金",
                    3,
                    "1500円",
                    "まとめ買い",
                    "2026-09-28T00:00:00+00:00",
                    "123456789",
                ),
            )

    def test_parse_quantity_requires_a_positive_integer(self) -> None:
        self.assertEqual(parse_quantity("3"), 3)

        with self.assertRaises(ValueError):
            parse_quantity("0")

        with self.assertRaises(ValueError):
            parse_quantity("three")


if __name__ == "__main__":
    unittest.main()
