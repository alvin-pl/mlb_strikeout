import unittest

from kalshi_market_maker.orderbook import KalshiOrderBook


class OrderBookTests(unittest.TestCase):
    def test_no_bid_implies_yes_ask(self):
        book = KalshiOrderBook.from_api_response(
            "TEST",
            {
                "orderbook_fp": {
                    "yes_dollars": [["0.6000", "10.00"], ["0.5900", "4.00"]],
                    "no_dollars": [["0.3500", "7.00"], ["0.3400", "8.00"]],
                }
            },
        )

        stats = book.stats()

        self.assertEqual(stats.best_yes_bid, 60)
        self.assertEqual(stats.best_yes_ask, 65)
        self.assertEqual(stats.mid_price, 62.5)
        self.assertEqual(stats.spread, 5)
        self.assertEqual(stats.yes_bid_liquidity, 10.0)
        self.assertEqual(stats.yes_ask_liquidity, 7.0)

    def test_websocket_snapshot_and_delta_update_local_book(self):
        book = KalshiOrderBook.from_ws_snapshot(
            {
                "type": "orderbook_snapshot",
                "seq": 1,
                "msg": {
                    "market_ticker": "TEST",
                    "market_id": "00000000-0000-0000-0000-000000000000",
                    "yes_dollars_fp": [["0.6000", "10.00"]],
                    "no_dollars_fp": [["0.3500", "7.00"]],
                },
            }
        )

        book.apply_delta({"type": "orderbook_delta", "seq": 2, "msg": {"market_ticker": "TEST", "side": "yes", "price": 60, "delta": "-10.00"}})

        self.assertIsNone(book.best_yes_bid())
        self.assertEqual(book.sequence, 2)


if __name__ == "__main__":
    unittest.main()
