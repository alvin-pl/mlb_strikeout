"""Command line entrypoint for the paper-first Kalshi bot."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import KalshiConfig, MarketConfig, load_config
from .kalshi_client import KalshiClient, KalshiClientError
from .live_broker import LiveBroker, LiveTradingNotEnabled
from .orderbook import KalshiOrderBook
from .paper_broker import PaperBroker
from .risk import RiskManager
from .strategy import ManualFairValueProvider, PassiveQuoteStrategy


def log_event(config: KalshiConfig, event_type: str, payload: dict[str, object]) -> None:
    path = Path(config.log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.time(), "type": event_type, **payload}
    with path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, sort_keys=True) + "\n")


def seeded_book(market: MarketConfig) -> KalshiOrderBook:
    book = KalshiOrderBook(market_ticker=market.ticker)
    book.replace_levels(market.seed_yes_bids, "yes")
    book.replace_levels(market.seed_no_bids, "no")
    return book


def load_initial_book(config: KalshiConfig, client: KalshiClient, market: MarketConfig) -> KalshiOrderBook:
    if config.has_api_credentials:
        try:
            return KalshiOrderBook.from_api_response(market.ticker, client.get_orderbook(market.ticker))
        except KalshiClientError as exc:
            log_event(config, "market_data_error", {"ticker": market.ticker, "error": str(exc)})
    return seeded_book(market)


def run_once(config: KalshiConfig, live_flag: bool) -> int:
    client = KalshiClient(config)
    risk = RiskManager(config.risk)
    strategy = PassiveQuoteStrategy(config.strategy)
    fair_values = ManualFairValueProvider({market.ticker: market.fair_value_cents for market in config.markets})
    books = {market.ticker: load_initial_book(config, client, market) for market in config.markets}

    if config.paper_trading:
        broker = PaperBroker(config.fee_cents_per_contract)
        live_order_ids: list[str] = []
    else:
        try:
            live_broker = LiveBroker(config, client, live_flag)
        except LiveTradingNotEnabled as exc:
            print(f"Refusing live trading: {exc}", file=sys.stderr)
            return 2
        broker = live_broker
        live_order_ids = []

    for market in config.markets:
        book = books[market.ticker]
        fair_value = fair_values.fair_value_cents(market.ticker)
        position = broker.position(market.ticker) if isinstance(broker, PaperBroker) else 0
        pnl = broker.mark_to_market_pnl_cents(books) if isinstance(broker, PaperBroker) else 0
        stats = book.stats()
        log_event(
            config,
            "book_stats",
            {
                "ticker": market.ticker,
                "best_yes_bid": stats.best_yes_bid,
                "best_yes_ask": stats.best_yes_ask,
                "mid_price": stats.mid_price,
                "spread": stats.spread,
                "yes_bid_liquidity": stats.yes_bid_liquidity,
                "yes_ask_liquidity": stats.yes_ask_liquidity,
            },
        )

        quotes = strategy.generate_quotes(book, fair_value, position, min(market.max_position, config.risk.max_position_per_market))
        for quote in quotes:
            decision = risk.check_quote(quote, book, market, position, broker.open_order_count() if isinstance(broker, PaperBroker) else len(live_order_ids), pnl)
            if not decision.allowed:
                log_event(config, "quote_rejected", {"quote": quote.__dict__, "reason": decision.reason})
                continue
            if isinstance(broker, PaperBroker):
                order = broker.place_order(quote)
                log_event(config, "paper_order_placed", {"order_id": order.order_id, "quote": quote.__dict__})
            else:
                order = broker.place_order(quote)
                order_id = str(order.get("order", {}).get("order_id", order.get("order_id", "")))
                if order_id:
                    live_order_ids.append(order_id)
                log_event(config, "live_order_placed", {"response": order, "quote": quote.__dict__})

    if isinstance(broker, PaperBroker):
        fills = broker.simulate_fills(books)
        for fill in fills:
            log_event(config, "paper_fill", fill.__dict__)
            log_event(
                config,
                "paper_position_pnl",
                {
                    "ticker": fill.market_ticker,
                    "position": broker.position(fill.market_ticker),
                    "pnl_cents": broker.mark_to_market_pnl_cents(books),
                },
            )

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Educational paper-first Kalshi market-making bot")
    parser.add_argument("--config", default="kalshi_market_maker/example_config.json")
    parser.add_argument("--live", action="store_true", help="Required, along with config confirmation, before live trading")
    parser.add_argument("--once", action="store_true", help="Run one quote cycle and exit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if config.paper_trading and args.live:
        print("Ignoring --live because config keeps paper_trading=true; no real orders will be sent.")
    if not args.once:
        print("This first version supports --once for safe paper-mode dry runs.", file=sys.stderr)
        return 2
    return run_once(config, args.live)


if __name__ == "__main__":
    raise SystemExit(main())
