import unittest

from kalshi_market_maker.config import StrategyConfig
from kalshi_market_maker.orderbook import KalshiOrderBook
from kalshi_market_maker.strategy import ManualFairValueProvider, PassiveQuoteStrategy


class StrategyTests(unittest.TestCase):
    def test_manual_fair_value_and_edge_quotes(self):
        fair_values = ManualFairValueProvider({"TEST": 63})
        book = KalshiOrderBook("TEST")
        book.replace_levels([["0.5900", "20.00"]], "yes")
        book.replace_levels([["0.3500", "20.00"]], "no")
        strategy = PassiveQuoteStrategy(StrategyConfig(min_edge_cents=2, quote_size=3))

        quotes = strategy.generate_quotes(book, fair_values.fair_value_cents("TEST"), position=0, max_position=10)

        self.assertEqual([quote.side for quote in quotes], ["buy_yes", "sell_yes"])
        self.assertEqual(quotes[0].price_cents, 60)
        self.assertLessEqual(quotes[0].price_cents, 61)
        self.assertEqual(quotes[1].price_cents, 65)
        self.assertGreaterEqual(quotes[1].price_cents, 65)

    def test_inventory_skew_makes_long_book_less_eager_to_buy(self):
        book = KalshiOrderBook("TEST")
        book.replace_levels([["0.5900", "20.00"]], "yes")
        book.replace_levels([["0.3500", "20.00"]], "no")
        strategy = PassiveQuoteStrategy(StrategyConfig(min_edge_cents=2, quote_size=3, inventory_skew_cents=4))

        flat_buy = [quote for quote in strategy.generate_quotes(book, 63, position=0, max_position=10) if quote.side == "buy_yes"][0]
        long_buy = [quote for quote in strategy.generate_quotes(book, 63, position=8, max_position=10) if quote.side == "buy_yes"][0]

        self.assertLess(long_buy.price_cents, flat_buy.price_cents)


if __name__ == "__main__":
    unittest.main()
