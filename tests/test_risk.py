import os
import unittest

from kalshi_market_maker.config import MarketConfig, RiskConfig
from kalshi_market_maker.orderbook import KalshiOrderBook
from kalshi_market_maker.risk import RiskManager
from kalshi_market_maker.strategy import Quote


class RiskTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("KALSHI_TEST_KILL_SWITCH", None)
        self.book = KalshiOrderBook("TEST")
        self.book.replace_levels([["0.5900", "20.00"]], "yes")
        self.book.replace_levels([["0.3500", "20.00"]], "no")
        self.market = MarketConfig(ticker="TEST", fair_value_cents=63, max_position=5)
        self.quote = Quote("TEST", "buy_yes", 60, 2, 63, "test")

    def test_rejects_position_limit(self):
        risk = RiskManager(RiskConfig(max_position_per_market=5))

        decision = risk.check_quote(self.quote, self.book, self.market, position=4, open_order_count=0, daily_pnl_cents=0)

        self.assertFalse(decision.allowed)
        self.assertIn("position", decision.reason)

    def test_rejects_daily_loss_limit(self):
        risk = RiskManager(RiskConfig(max_total_daily_loss_cents=100))

        decision = risk.check_quote(self.quote, self.book, self.market, position=0, open_order_count=0, daily_pnl_cents=-100)

        self.assertFalse(decision.allowed)
        self.assertIn("daily loss", decision.reason)

    def test_rejects_kill_switch(self):
        os.environ["KALSHI_TEST_KILL_SWITCH"] = "true"
        risk = RiskManager(RiskConfig(kill_switch_path="KALSHI_TEST_KILL_SWITCH"))

        decision = risk.check_quote(self.quote, self.book, self.market, position=0, open_order_count=0, daily_pnl_cents=0)

        self.assertFalse(decision.allowed)
        self.assertIn("kill switch", decision.reason)


if __name__ == "__main__":
    unittest.main()
