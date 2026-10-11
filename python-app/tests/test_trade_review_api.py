import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from python_app.trade_review_api import (
    TradeReviewAPIError,
    get_pending_exchanges,
    get_pending_trades,
    update_exchange_status,
    update_trade_status,
)


def response(payload):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(payload).encode()

    return FakeResponse()


def pending_trade(trade_id, item_count=1):
    return {
        "id": trade_id,
        "tradeType": "bundle" if item_count > 1 else "single",
        "totalPrice": 1200,
        "status": "pending",
        "submittedAt": "2026-10-10T00:00:00Z",
        "items": [
            {
                "position": position,
                "characterId": "c002",
                "characterNumber": "001",
                "characterName": "ゴブリン",
                "rarity": "common",
                "level": 10,
                "mutation": "normal",
                "quantity": 1,
            }
            for position in range(1, item_count + 1)
        ],
    }


def pending_exchange(exchange_id, item_count=1):
    return {
        "id": exchange_id,
        "exchangeDescription": f"交換内容 {exchange_id}",
        "status": "pending",
        "submittedAt": "2026-10-10T00:00:00Z",
        "items": [
            {
                "position": position,
                "characterId": "c002",
                "characterNumber": "001",
                "characterName": "ゴブリン",
                "rarity": "common",
                "level": 10,
                "mutation": "normal",
                "quantity": 1,
            }
            for position in range(1, item_count + 1)
        ],
    }


class TradeReviewAPITests(unittest.TestCase):
    @patch("python_app.trade_review_api.urlopen")
    def test_reads_pending_trades_with_the_worker_admin_header(self, urlopen):
        urlopen.return_value = response(
            {
                "ok": True,
                "trades": [
                    {
                        "id": 7,
                        "tradeType": "single",
                        "totalPrice": 1200,
                        "status": "pending",
                        "submittedAt": "2026-10-10T00:00:00Z",
                        "items": [
                            {
                                "characterId": "c002",
                                "characterNumber": "001",
                                "characterName": "ゴブリン",
                                "rarity": "common",
                                "level": 10,
                                "mutation": "normal",
                                "quantity": 1,
                            }
                        ],
                    }
                ],
                "nextCursor": None,
                "throughCursor": 7,
                "hasMore": False,
            }
        )

        result = get_pending_trades(admin_key="test-admin-key")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].trade_id, 7)
        self.assertEqual(result[0].items[0].character_number, "001")
        request = urlopen.call_args.args[0]
        self.assertEqual(
            parse_qs(urlparse(request.full_url).query),
            {"status": ["pending"], "limit": ["50"], "after": ["0"]},
        )
        self.assertEqual(request.get_header("X-admin-key"), "test-admin-key")
        self.assertNotIn("test-admin-key", repr(result))

    @patch("python_app.trade_review_api.urlopen")
    def test_fetches_all_oldest_first_trade_pages_with_multiple_items(self, urlopen):
        urlopen.side_effect = [
            response(
                {
                    "ok": True,
                    "trades": [pending_trade(trade_id, 3) for trade_id in range(1, 51)],
                    "nextCursor": 50,
                    "throughCursor": 51,
                    "hasMore": True,
                }
            ),
            response(
                {
                    "ok": True,
                    "trades": [pending_trade(51, 3)],
                    "nextCursor": None,
                    "throughCursor": 51,
                    "hasMore": False,
                }
            ),
        ]

        result = get_pending_trades(admin_key="test-admin-key")

        self.assertEqual([trade.trade_id for trade in result], list(range(1, 52)))
        self.assertTrue(all(len(trade.items) == 3 for trade in result))
        self.assertEqual(urlopen.call_count, 2)
        queries = [
            parse_qs(urlparse(call.args[0].full_url).query)
            for call in urlopen.call_args_list
        ]
        self.assertEqual(queries[0]["after"], ["0"])
        self.assertEqual(queries[1]["after"], ["50"])
        self.assertNotIn("through", queries[0])
        self.assertEqual(queries[1]["through"], ["51"])

    @patch("python_app.trade_review_api.urlopen")
    def test_sends_terminal_decision_and_reviewer_identity(self, urlopen):
        urlopen.return_value = response({"ok": True})

        update_trade_status(
            7,
            "approved",
            reviewer_id="discord-123",
            admin_key="test-admin-key",
        )

        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/trades/7/status"))
        self.assertEqual(
            json.loads(request.data),
            {"status": "approved", "reviewerId": "discord-123"},
        )

    @patch("python_app.trade_review_api.urlopen")
    def test_reports_worker_conflict_when_trade_was_already_reviewed(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://worker.example/trades/7/status",
            409,
            "Conflict",
            {},
            io.BytesIO(b'{"ok":false,"error":"Trade has already been reviewed"}'),
        )

        with self.assertRaisesRegex(TradeReviewAPIError, "すでに審査"):
            update_trade_status(
                7,
                "rejected",
                reviewer_id="discord-123",
                admin_key="test-admin-key",
            )

    def test_rejects_non_pending_records_from_worker(self):
        with patch(
            "python_app.trade_review_api.urlopen",
            return_value=response(
                {
                    "ok": True,
                    "trades": [
                        {
                            "id": 2,
                            "tradeType": "single",
                            "totalPrice": 1200,
                            "status": "approved",
                            "submittedAt": "2026-10-10T00:00:00Z",
                            "items": [
                                {
                                    "characterId": "c002",
                                    "characterNumber": "001",
                                    "characterName": "ゴブリン",
                                    "rarity": "common",
                                    "level": 10,
                                    "mutation": "normal",
                                    "quantity": 1,
                                }
                            ],
                        }
                    ],
                    "nextCursor": None,
                    "throughCursor": 2,
                    "hasMore": False,
                }
            ),
        ):
            with self.assertRaisesRegex(TradeReviewAPIError, "未審査"):
                get_pending_trades(admin_key="test-admin-key")

    def test_does_not_allow_pending_as_a_terminal_decision(self):
        with self.assertRaises(ValueError):
            update_trade_status(
                7,
                "pending",
                reviewer_id="discord-123",
                admin_key="test-admin-key",
            )

    @patch("python_app.trade_review_api.urlopen")
    def test_reads_pending_exchange_details_without_a_price(self, urlopen):
        urlopen.return_value = response(
            {
                "ok": True,
                "exchanges": [
                    {
                        "id": 23,
                        "exchangeDescription": "交換対象についての説明",
                        "status": "pending",
                        "submittedAt": "2026-10-10T00:00:00Z",
                        "items": [
                            {
                                "characterId": "c002",
                                "characterNumber": "001",
                                "characterName": "ゴブリン",
                                "rarity": "common",
                                "level": 10,
                                "mutation": "normal",
                                "quantity": 1,
                            }
                        ],
                    }
                ],
                "nextCursor": None,
                "throughCursor": 23,
                "hasMore": False,
            }
        )

        result = get_pending_exchanges(admin_key="test-admin-key")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].exchange_id, 23)
        self.assertEqual(result[0].exchange_description, "交換対象についての説明")
        self.assertEqual(result[0].items[0].character_number, "001")
        self.assertNotIn("price", repr(result[0]).lower())
        self.assertTrue(
            parse_qs(urlparse(urlopen.call_args.args[0].full_url).query)
            == {"status": ["pending"], "limit": ["50"], "after": ["0"]}
        )

    @patch("python_app.trade_review_api.urlopen")
    def test_fetches_all_oldest_first_exchange_pages_with_multiple_items(self, urlopen):
        urlopen.side_effect = [
            response(
                {
                    "ok": True,
                    "exchanges": [
                        pending_exchange(exchange_id, 3)
                        for exchange_id in range(1, 51)
                    ],
                    "nextCursor": 50,
                    "throughCursor": 51,
                    "hasMore": True,
                }
            ),
            response(
                {
                    "ok": True,
                    "exchanges": [pending_exchange(51, 3)],
                    "nextCursor": None,
                    "throughCursor": 51,
                    "hasMore": False,
                }
            ),
        ]

        result = get_pending_exchanges(admin_key="test-admin-key")

        self.assertEqual(
            [exchange.exchange_id for exchange in result], list(range(1, 52))
        )
        self.assertTrue(all(len(exchange.items) == 3 for exchange in result))
        self.assertEqual(urlopen.call_count, 2)
        queries = [
            parse_qs(urlparse(call.args[0].full_url).query)
            for call in urlopen.call_args_list
        ]
        self.assertEqual(queries[0]["after"], ["0"])
        self.assertEqual(queries[1]["after"], ["50"])
        self.assertEqual(queries[1]["through"], ["51"])

    @patch("python_app.trade_review_api.urlopen")
    def test_rejects_workers_without_pagination_metadata(self, urlopen):
        urlopen.return_value = response({"ok": True, "trades": []})

        with self.assertRaisesRegex(TradeReviewAPIError, "ページング"):
            get_pending_trades(admin_key="test-admin-key")

    @patch("python_app.trade_review_api.urlopen")
    def test_rejects_a_changed_review_snapshot_boundary(self, urlopen):
        urlopen.side_effect = [
            response(
                {
                    "ok": True,
                    "trades": [pending_trade(trade_id) for trade_id in range(1, 51)],
                    "nextCursor": 50,
                    "throughCursor": 51,
                    "hasMore": True,
                }
            ),
            response(
                {
                    "ok": True,
                    "trades": [pending_trade(51)],
                    "nextCursor": None,
                    "throughCursor": 52,
                    "hasMore": False,
                }
            ),
        ]

        with self.assertRaisesRegex(TradeReviewAPIError, "ページカーソル"):
            get_pending_trades(admin_key="test-admin-key")

    @patch("python_app.trade_review_api.urlopen")
    def test_rejects_non_monotonic_pending_page_ids(self, urlopen):
        urlopen.return_value = response(
            {
                "ok": True,
                "trades": [pending_trade(1), pending_trade(3), pending_trade(2)],
                "nextCursor": 2,
                "throughCursor": 3,
                "hasMore": True,
            }
        )

        with self.assertRaisesRegex(TradeReviewAPIError, "ページ順序"):
            get_pending_trades(admin_key="test-admin-key")

    @patch("python_app.trade_review_api.urlopen")
    def test_sends_authorized_exchange_review_decision(self, urlopen):
        urlopen.return_value = response({"ok": True})

        update_exchange_status(
            23,
            "rejected",
            reviewer_id="discord-123",
            admin_key="test-admin-key",
        )

        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/exchanges/23/status"))
        self.assertEqual(
            json.loads(request.data),
            {"status": "rejected", "reviewerId": "discord-123"},
        )


if __name__ == "__main__":
    unittest.main()
