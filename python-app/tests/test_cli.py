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
    SetCharacterModal,
    SetTradeOptionsView,
    SetTotalAmountModal,
    SingleTradeModal,
    TradeCharacter,
    TradeEntryView,
    TradeTypeView,
    create_bot,
    format_trade_confirmation,
    format_recent_trades,
    get_discord_token,
    get_recent_trades,
    initialize_database,
    parse_quantity,
    save_trade,
    save_set_trade,
    transaction_type_for_quantity,
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


class FakeInteractionResponse:
    def __init__(self) -> None:
        self.message_args: tuple[object, ...] = ()
        self.message_kwargs: dict[str, object] = {}
        self.modal: object | None = None

    async def send_message(self, *args: object, **kwargs: object) -> None:
        self.message_args = args
        self.message_kwargs = kwargs

    async def send_modal(self, modal: object) -> None:
        self.modal = modal


class FakeInteraction:
    def __init__(self) -> None:
        self.response = FakeInteractionResponse()


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
        self.assertIsNotNone(bot.tree.get_command("trades"))
        self.assertFalse(bot.intents.message_content)

    def test_trade_view_has_a_persistent_button(self) -> None:
        view = TradeEntryView()

        self.assertIsNone(view.timeout)
        self.assertEqual(len(view.children), 1)
        self.assertEqual(view.children[0].label, "取引記入")

    def test_single_trade_modal_has_the_requested_fields(self) -> None:
        modal = SingleTradeModal()

        self.assertEqual(modal.title, "単体・まとめ買い")
        self.assertEqual(len(modal.children), 5)
        self.assertEqual(
            [child.label for child in modal.children],
            ["キャラ名", "変異", "レベル", "個数", "合計金額"],
        )
        self.assertEqual(modal.children[4].placeholder, "例: 1.5k")

    def test_trade_entry_button_opens_two_choice_view(self) -> None:
        view = TradeTypeView()

        self.assertEqual(
            [child.label for child in view.children],
            ["単体・まとめ買い", "セット"],
        )

    def test_entry_button_sends_ephemeral_type_choices(self) -> None:
        import asyncio

        view = TradeEntryView()
        interaction = FakeInteraction()
        asyncio.run(view.children[0].callback(interaction))

        self.assertTrue(interaction.response.message_kwargs["ephemeral"])
        self.assertIsInstance(
            interaction.response.message_kwargs["view"],
            TradeTypeView,
        )

    def test_type_choices_open_the_matching_modal(self) -> None:
        import asyncio

        single_interaction = FakeInteraction()
        asyncio.run(
            TradeTypeView().children[0].callback(single_interaction)
        )
        self.assertIsInstance(
            single_interaction.response.modal,
            SingleTradeModal,
        )

        set_interaction = FakeInteraction()
        asyncio.run(
            TradeTypeView().children[1].callback(set_interaction)
        )
        self.assertIsInstance(
            set_interaction.response.modal,
            SetCharacterModal,
        )

    def test_set_trade_uses_per_character_modal_and_caps_at_three(self) -> None:
        character_modal = SetCharacterModal()
        self.assertEqual(
            [child.label for child in character_modal.children],
            ["キャラ名", "変異", "レベル", "個数"],
        )

        one_character = TradeCharacter("A", "通常", "10", 1)
        three_character_view = SetTradeOptionsView(
            [
                one_character,
                TradeCharacter("B", "金", "20", 2),
                TradeCharacter("C", "虹", "30", 1),
            ]
        )
        add_button = next(
            child for child in three_character_view.children
            if child.label == "キャラを追加"
        )
        self.assertTrue(add_button.disabled)

        total_modal = SetTotalAmountModal([one_character])
        self.assertEqual(
            [child.label for child in total_modal.children],
            ["セット全体の合計金額"],
        )
        self.assertEqual(total_modal.children[0].placeholder, "例: 1.5k")

    def test_trade_confirmation_escapes_user_input(self) -> None:
        confirmation = format_trade_confirmation(
            "A @everyone",
            "**金**",
            "2",
            "1,000円",
            TRANSACTION_TYPES[0],
            "50",
        )

        self.assertIn("A @", confirmation)
        self.assertNotIn("@everyone", confirmation)
        self.assertIn("\\*\\*金\\*\\*", confirmation)
        self.assertIn("通常", confirmation)
        self.assertIn("レベル", confirmation)

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
            with sqlite3.connect(database_path) as connection:
                detail_table = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'trade_characters'"
                ).fetchone()
            self.assertEqual(detail_table, ("trade_characters",))

    def test_initialize_database_migrates_legacy_rows_without_losing_data(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.sqlite3"
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE trades (
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
                connection.execute(
                    """
                    INSERT INTO trades (
                        id, character_name, mutation, quantity, total_amount,
                        transaction_type, registered_at,
                        registered_by_discord_user_id
                    )
                    VALUES (7, 'Old Character', '金', 2, '900円',
                            'まとめ買い', '2026-09-28T00:00:00+00:00', '123')
                    """
                )

            initialize_database(database_path)
            initialize_database(database_path)

            with sqlite3.connect(database_path) as connection:
                row = connection.execute(
                    """
                    SELECT id, character_name, mutation, quantity, total_amount,
                           transaction_type, registered_at,
                           registered_by_discord_user_id, level
                    FROM trades WHERE id = 7
                    """
                ).fetchone()
                schema = connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'trades'"
                ).fetchone()[0]

            self.assertEqual(
                row,
                (
                    7,
                    "Old Character",
                    "金",
                    2,
                    "900円",
                    "まとめ買い",
                    "2026-09-28T00:00:00+00:00",
                    "123",
                    "",
                ),
            )
            self.assertIn("単体", schema)

            new_id = save_trade(
                character_name="New Character",
                mutation="通常",
                quantity=1,
                total_amount="1k",
                transaction_type="単体",
                registered_by_discord_user_id="123",
                db_path=database_path,
                level="50",
            )
            with sqlite3.connect(database_path) as connection:
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT id FROM trades WHERE id = ?",
                        (new_id,),
                    ).fetchone()
                )
            self.assertGreater(new_id, 7)
            with sqlite3.connect(database_path) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )

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
                level="50",
            )

            with sqlite3.connect(database_path) as connection:
                row = connection.execute(
                    """
                    SELECT character_name, mutation, quantity, total_amount,
                           transaction_type, registered_at,
                           registered_by_discord_user_id, level
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
                    "50",
                ),
            )

    def test_save_set_trade_stores_bundle_total_and_character_details(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "trades.sqlite3"
            characters = (
                TradeCharacter("Character A", "金", "50", 1),
                TradeCharacter("Character B", "虹", "60", 2),
                TradeCharacter("Character C", "通常", "70", 1),
            )

            trade_id = save_set_trade(
                characters=characters,
                total_amount="4.5k",
                registered_by_discord_user_id="123456789",
                registered_at="2026-09-28T00:00:00+00:00",
                db_path=database_path,
            )

            with sqlite3.connect(database_path) as connection:
                parent = connection.execute(
                    """
                    SELECT transaction_type, total_amount, quantity
                    FROM trades WHERE id = ?
                    """,
                    (trade_id,),
                ).fetchone()
                details = connection.execute(
                    """
                    SELECT character_name, mutation, level, quantity
                    FROM trade_characters
                    WHERE trade_id = ?
                    ORDER BY position
                    """,
                    (trade_id,),
                ).fetchall()
                detail_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(trade_characters)"
                    ).fetchall()
                }

            self.assertEqual(parent, ("セット", "4.5k", 4))
            self.assertEqual(
                details,
                [
                    ("Character A", "金", "50", 1),
                    ("Character B", "虹", "60", 2),
                    ("Character C", "通常", "70", 1),
                ],
            )
            self.assertNotIn("total_amount", detail_columns)

            recent_trade = get_recent_trades(db_path=database_path)[0]
            embed = format_recent_trades([recent_trade])
            self.assertIn("Character A", embed.fields[0].value)
            self.assertIn("Character C", embed.fields[0].value)
            self.assertIn("4.5k", embed.fields[0].value)

    def test_save_set_trade_rejects_more_than_three_characters(self) -> None:
        characters = tuple(
            TradeCharacter(f"Character {index}", "通常", "1", 1)
            for index in range(4)
        )
        with self.assertRaises(ValueError):
            save_set_trade(
                characters=characters,
                total_amount="1k",
                registered_by_discord_user_id="123",
                db_path=Path("/not-created/trades.sqlite3"),
            )

    def test_get_recent_trades_returns_latest_twenty_without_writing(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "trades.sqlite3"
            initialize_database(database_path)

            for index in range(1, 23):
                save_trade(
                    character_name=f"Character {index}",
                    mutation="通常",
                    quantity=index,
                    total_amount=f"{index * 100}円",
                    transaction_type="通常",
                    registered_by_discord_user_id=str(index),
                    registered_at=f"2026-09-28T00:00:{index:02d}+00:00",
                    db_path=database_path,
                )

            recent_trades = get_recent_trades(db_path=database_path)

            self.assertEqual(len(recent_trades), 20)
            self.assertEqual(recent_trades[0]["character_name"], "Character 22")
            self.assertEqual(recent_trades[-1]["character_name"], "Character 3")

    def test_get_recent_trades_does_not_create_a_missing_database(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "missing.sqlite3"

            self.assertEqual(get_recent_trades(db_path=database_path), [])
            self.assertFalse(database_path.exists())

    def test_format_recent_trades_displays_requested_fields(self) -> None:
        embed = format_recent_trades(
            [
                {
                    "character_name": "Trade Value",
                    "mutation": "金",
                    "quantity": 3,
                    "total_amount": "1500円",
                    "transaction_type": "まとめ買い",
                    "registered_at": "2026-09-28T00:00:00+00:00",
                }
            ]
        )

        self.assertEqual(embed.title, "最新の取引データ")
        self.assertEqual(len(embed.fields), 1)
        self.assertIn("変異", embed.fields[0].value)
        self.assertIn("登録日時", embed.fields[0].value)

    def test_parse_quantity_requires_a_positive_integer(self) -> None:
        self.assertEqual(parse_quantity("3"), 3)

        with self.assertRaises(ValueError):
            parse_quantity("0")

        with self.assertRaises(ValueError):
            parse_quantity("three")

    def test_transaction_type_is_inferred_from_quantity(self) -> None:
        self.assertEqual(transaction_type_for_quantity(1), "単体")
        self.assertEqual(transaction_type_for_quantity(2), "まとめ買い")

        with self.assertRaises(ValueError):
            transaction_type_for_quantity(0)


if __name__ == "__main__":
    unittest.main()
