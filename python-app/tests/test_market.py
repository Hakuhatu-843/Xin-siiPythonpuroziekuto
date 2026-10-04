import sqlite3
import tempfile
import unittest
from pathlib import Path

from python_app.market import (
    PriceObservation,
    calculate_mutation_multiplier,
    estimate_market_price,
    parse_market_amount,
    price_per_unit,
    load_verified_character_observations,
)


class MarketTests(unittest.TestCase):
    def test_parse_market_amount(self):
        self.assertEqual(parse_market_amount("1.5k"), 1500)
        self.assertEqual(parse_market_amount("1,500円"), 1500)
        self.assertEqual(parse_market_amount(2500), 2500)

    def test_price_per_unit(self):
        self.assertEqual(price_per_unit("1.5k", 3), 500)

    def test_exact_level_estimate_uses_median(self):
        observations = [
            PriceObservation("A", 10, 100),
            PriceObservation("A", 10, 120),
            PriceObservation("A", 10, 110),
        ]
        result = estimate_market_price(observations, level=10)
        self.assertEqual(result.price, 110)
        self.assertEqual(result.transaction_count, 3)
        self.assertEqual(result.confidence, "低")
        self.assertFalse(result.is_estimated)

    def test_missing_level_does_not_invent_price(self):
        observations = [PriceObservation("A", 10, 100)]
        result = estimate_market_price(observations, level=20)
        self.assertEqual(result.price, 0)
        self.assertTrue(result.is_estimated)
        self.assertEqual(result.confidence, "データ不足")

    def test_mutation_multiplier(self):
        self.assertEqual(calculate_mutation_multiplier(1000, 1800), 1.8)

    def test_load_verified_character_observations_includes_legacy_trade(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "trades.sqlite3"
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY,
                    character_name TEXT NOT NULL,
                    mutation TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    total_amount TEXT NOT NULL,
                    transaction_type TEXT NOT NULL,
                    level TEXT NOT NULL,
                    is_verified INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE trade_characters (
                    id INTEGER PRIMARY KEY,
                    trade_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    character_name TEXT NOT NULL,
                    mutation TEXT NOT NULL,
                    level TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    is_verified INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO trades
                VALUES (1, 'A', '通常', 2, '1000', 'まとめ買い', '10', 1)
                """
            )
            connection.commit()

            observations = load_verified_character_observations(
                connection, "A", "通常"
            )

            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0].level, 10)
            self.assertEqual(observations[0].price_per_unit, 500)
            connection.close()

    def test_load_verified_character_observations_ignores_set_and_unverified(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "trades.sqlite3"
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY,
                    character_name TEXT NOT NULL,
                    mutation TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    total_amount TEXT NOT NULL,
                    transaction_type TEXT NOT NULL,
                    level TEXT NOT NULL,
                    is_verified INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE trade_characters (
                    id INTEGER PRIMARY KEY,
                    trade_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    character_name TEXT NOT NULL,
                    mutation TEXT NOT NULL,
                    level TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    is_verified INTEGER NOT NULL
                )
                """
            )
            connection.executemany(
                "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (1, "A", "通常", 1, "1000", "セット", "10", 1),
                    (2, "A", "通常", 1, "900", "単体", "10", 0),
                ],
            )
            connection.commit()

            observations = load_verified_character_observations(
                connection, "A", "通常"
            )

            self.assertEqual(observations, [])
            connection.close()


if __name__ == "__main__":
    unittest.main()
