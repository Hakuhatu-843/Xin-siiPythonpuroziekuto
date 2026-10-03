import unittest

from python_app.market import (
    PriceObservation,
    calculate_mutation_multiplier,
    estimate_market_price,
    parse_market_amount,
    price_per_unit,
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


if __name__ == "__main__":
    unittest.main()
