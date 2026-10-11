import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from python_app.market_api import MarketAPIError, get_characters, get_market_snapshot


def snapshot_payload():
    return {
        "ok": True,
        "characterId": "l001",
        "characterName": "フェニックス",
        "level1Value": 50000,
        "levelMaxValue": None,
        "demandScore": 3.4,
        "sampleCount": 8,
        "level1SampleCount": 5,
        "levelMaxSampleCount": 2,
        "createdAt": "2026-10-04T15:00:00.000Z",
        "changes": {
            "level1Direction": "↑",
            "levelMaxDirection": "→",
            "demandDirection": "↓",
            "mutationDirections": {"normal": "→"},
            "hasPrevious": True,
        },
        "mutations": [
            {
                "mutation": "normal",
                "multiplier": 1.0,
                "sampleCount": 8,
                "direction": "→",
            }
        ],
    }


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class MarketAPITests(unittest.TestCase):

    @patch("python_app.market_api.urlopen")
    def test_fetches_canonical_character_catalog(self, urlopen):
        urlopen.return_value = FakeResponse({
            "ok": True,
            "characters": [
                {"characterNumber": "002", "characterName": "ピピ・ウォーターメロン", "englishName": "Pipi Watermelon", "rarity": "common"},
                {"characterNumber": "001", "characterName": "フルリフルラ", "englishName": "Fluriflura", "rarity": "common"},
            ],
        })
        result = get_characters(base_url="https://worker.example/")
        self.assertEqual([(item.character_number, item.english_name) for item in result], [("001", "Fluriflura"), ("002", "Pipi Watermelon")])
        self.assertEqual(result[0].japanese_name, "フルリフルラ")
        request, = urlopen.call_args.args
        self.assertEqual(request.full_url, "https://worker.example/characters")

    @patch("python_app.market_api.urlopen")
    def test_rejects_invalid_character_catalog_number(self, urlopen):
        urlopen.return_value = FakeResponse({"ok": True, "characters": [{"characterNumber": "A001", "characterName": "管理用キャラ", "englishName": "Management Character", "rarity": "ancient"}]})
        with self.assertRaisesRegex(MarketAPIError, "不正な項目"):
            get_characters()

    @patch("python_app.market_api.urlopen")
    def test_fetches_and_decodes_snapshot(self, urlopen):
        urlopen.return_value = FakeResponse(snapshot_payload())

        result = get_market_snapshot(
            "フェニックス", base_url="https://worker.example/", timeout=2.5
        )

        self.assertEqual(result.character_id, "l001")
        self.assertEqual(result.character_name, "フェニックス")
        self.assertEqual(result.level1_value, 50000)
        self.assertIsNone(result.level_max_value)
        self.assertEqual(result.demand_score, 3.4)
        self.assertEqual(result.level1_sample_count, 5)
        self.assertEqual(result.level_max_sample_count, 2)
        self.assertEqual(result.level1_direction, "↑")
        self.assertEqual(result.mutations[0].multiplier, 1.0)
        request, = urlopen.call_args.args
        self.assertIn("characterName=%E3%83%95", request.full_url)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 2.5)

    @patch("python_app.market_api.urlopen")
    def test_fetches_snapshot_by_character_number(self, urlopen):
        payload = snapshot_payload()
        payload["characterNumber"] = "001"
        urlopen.return_value = FakeResponse(payload)

        result = get_market_snapshot(character_number="001")

        self.assertEqual(result.character_number, "001")
        self.assertEqual(result.character_id, "l001")
        request, = urlopen.call_args.args
        self.assertTrue(request.full_url.endswith("characterNumber=001"))

    def test_rejects_invalid_or_ambiguous_character_lookup(self):
        with self.assertRaisesRegex(ValueError, "3桁"):
            get_market_snapshot(character_number="1")
        with self.assertRaisesRegex(ValueError, "どちらか"):
            get_market_snapshot()
        with self.assertRaisesRegex(ValueError, "どちらか"):
            get_market_snapshot("フェニックス", character_number="001")

    @patch("python_app.market_api.urlopen")
    def test_rejects_out_of_range_demand(self, urlopen):
        payload = snapshot_payload()
        payload["demandScore"] = 5.1
        urlopen.return_value = FakeResponse(payload)

        with self.assertRaisesRegex(MarketAPIError, "demandScore"):
            get_market_snapshot("フェニックス")

    @patch("python_app.market_api.urlopen")
    def test_reports_missing_snapshot(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://worker.example/market-snapshot",
            404,
            "Not Found",
            {},
            BytesIO(b'{"ok":false,"error":"Market snapshot not found"}'),
        )

        with self.assertRaisesRegex(MarketAPIError, "スナップショットがまだありません"):
            get_market_snapshot("フェニックス")

    @patch("python_app.market_api.urlopen")
    def test_reports_unregistered_character_number(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://worker.example/market-snapshot?characterNumber=999",
            404,
            "Not Found",
            {},
            BytesIO(b'{"ok":false,"error":"Character number not found"}'),
        )

        with self.assertRaisesRegex(MarketAPIError, "未登録のキャラクター番号"):
            get_market_snapshot(character_number="999")

    @patch("python_app.market_api.urlopen")
    def test_reports_timeout(self, urlopen):
        urlopen.side_effect = TimeoutError

        with self.assertRaisesRegex(MarketAPIError, "タイムアウト"):
            get_market_snapshot("フェニックス")


if __name__ == "__main__":
    unittest.main()
