import json
import unittest
from contextlib import redirect_stdout
from io import StringIO

from python_app.cli import build_greeting, main
from python_app.discord_bot import create_bot, get_discord_token


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
        self.assertFalse(bot.intents.message_content)


if __name__ == "__main__":
    unittest.main()
