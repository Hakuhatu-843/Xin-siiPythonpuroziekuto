import sqlite3
import unittest
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "worker" / "migrations"


class MarketCharacterNumberMigrationTests(unittest.TestCase):
    def test_number_mapping_preserves_existing_trade_and_character_ids(self):
        connection = sqlite3.connect(":memory:")
        try:
            migration_paths = sorted(MIGRATIONS_DIR.glob("*.sql"))
            for path in migration_paths:
                if path.name == "0005_market_character_numbers.sql":
                    connection.execute(
                        """
                        INSERT INTO trades (
                            id, trade_type, total_amount, status, submitted_at
                        ) VALUES (1, 'single', 2000, 'approved', '2026-10-05T00:00:00Z')
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO trade_characters (
                            trade_id, position, character_id, rarity, level,
                            mutation, quantity, is_verified
                        ) VALUES (1, 1, 'c002', 'common', 10, 'normal', 1, 1)
                        """
                    )
                    before = connection.execute(
                        """
                        SELECT id, character_id, level, mutation
                        FROM trade_characters WHERE trade_id = 1
                        """
                    ).fetchall()

                if path.name == "0007_snapshot_price_sample_counts.sql":
                    connection.execute(
                        """
                        INSERT INTO market_snapshots (
                            id, character_id, level1_value, level_max_value,
                            demand_score, sample_count, created_at
                        ) VALUES (
                            1, 'c002', 1200, 2000, 2.5, 4,
                            '2026-10-05T00:00:00Z'
                        )
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO market_snapshot_mutations (
                            snapshot_id, mutation, multiplier, sample_count
                        ) VALUES (1, 'normal', 1.0, 4)
                        """
                    )

                connection.executescript(path.read_text(encoding="utf-8"))

            mappings = connection.execute(
                """
                SELECT numbers.character_number, names.character_id,
                       names.character_name
                FROM market_character_numbers AS numbers
                JOIN market_character_names AS names
                  ON names.character_id = numbers.character_id
                ORDER BY numbers.character_number
                """
            ).fetchall()
            self.assertEqual(
                mappings,
                [("001", "c002", "ゴブリン"), ("002", "l001", "フェニックス")],
            )
            after = connection.execute(
                """
                SELECT id, character_id, level, mutation
                FROM trade_characters WHERE trade_id = 1
                """
            ).fetchall()
            self.assertEqual(after, before)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute(
                    "SELECT level1_value, level_max_value, demand_score, "
                    "level1_sample_count, level_max_sample_count "
                    "FROM market_snapshots WHERE id = 1"
                ).fetchone(),
                (1200, 2000, 2.5, None, None),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM market_snapshot_mutations "
                    "WHERE snapshot_id = 1"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'trade_review_actions'"
                ).fetchone(),
                ("trade_review_actions",),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT submission_id, reviewed_by, reviewed_at, review_token "
                    "FROM trades WHERE id = 1"
                ).fetchone(),
                (None, None, None, None),
            )
            submission_id = "76e7b900-2882-46b5-868d-1cc3648c6e8a"
            connection.execute(
                """
                INSERT INTO trades (
                    trade_type, total_amount, status, submitted_at, submission_id
                ) VALUES ('single', 100, 'pending', '2026-10-06T00:00:00Z', ?)
                """,
                (submission_id,),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO trades (
                        trade_type, total_amount, status, submitted_at, submission_id
                    ) VALUES ('single', 100, 'pending', '2026-10-06T00:00:00Z', ?)
                    """,
                    (submission_id,),
                )
            connection.execute(
                """
                INSERT INTO trade_review_actions
                    (trade_id, status, reviewer_id, reviewed_at)
                VALUES (1, 'approved', 'discord-123', '2026-10-06T00:00:00Z')
                """
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO trade_review_actions
                        (trade_id, status, reviewer_id, reviewed_at)
                    VALUES (1, 'rejected', 'discord-456', '2026-10-06T00:01:00Z')
                    """
                )

            exchange_submission_id = "66e7b900-2882-46b5-868d-1cc3648c6e8a"
            connection.execute(
                """
                INSERT INTO exchange_submissions (
                    submission_id, exchange_description, status, submitted_at
                ) VALUES (?, 'ゴブリンとの交換', 'pending', '2026-10-06T00:00:00Z')
                """,
                (exchange_submission_id,),
            )
            connection.execute(
                """
                INSERT INTO exchange_submission_characters (
                    submission_id, position, character_id, rarity, level,
                    mutation, quantity
                ) VALUES (1, 1, 'c002', 'common', 10, 'normal', 1)
                """
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO exchange_submissions (
                    submission_id, exchange_description, status, submitted_at
                    ) VALUES (?, 'duplicate', 'pending', '2026-10-06T00:00:00Z')
                    """,
                    (exchange_submission_id,),
                )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0], 2
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM exchange_submissions").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM trade_characters").fetchone()[0],
                1,
            )

            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO market_character_numbers
                    (character_number, character_id) VALUES ('001', 'l001')
                    """
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO market_character_numbers
                    (character_number, character_id) VALUES ('003', 'c002')
                    """
                )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
