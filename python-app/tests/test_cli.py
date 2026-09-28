import json
import unittest
from contextlib import redirect_stdout
from io import StringIO

from python_app.cli import build_greeting, main
from python_app.discord_bot import (
    TRANSACTION_TYPES,
    TradeEntryModal,
    TradeEntryView,
    create_bot,
    format_trade_confirmation,
    get_discord_token,
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


if __name__ == "__main__":
    unittest.main()
