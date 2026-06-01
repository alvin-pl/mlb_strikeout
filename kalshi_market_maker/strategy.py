"""Quote generation for a simple passive market-making strategy."""

from __future__ import annotations

from dataclasses import dataclass

from .config import StrategyConfig
from .orderbook import KalshiOrderBook


@dataclass(frozen=True)
class Quote:
    market_ticker: str
    side: str
    price_cents: int
    quantity: int
    fair_value_cents: int
    reason: str


class ManualFairValueProvider:
    """Returns manually configured fair values.

    This object is intentionally simple so a future model can replace it with a
    class that has the same `fair_value_cents(ticker)` method.
    """

    def __init__(self, values_by_ticker: dict[str, int]):
        self._values_by_ticker = dict(values_by_ticker)

    def fair_value_cents(self, ticker: str) -> int:
        value = self._values_by_ticker[ticker]
        if not 1 <= value <= 99:
            raise ValueError(f"Fair value for {ticker} must be between 1 and 99 cents")
        return value

    def update(self, ticker: str, fair_value_cents: int) -> None:
        if not 1 <= fair_value_cents <= 99:
            raise ValueError("Fair value must be between 1 and 99 cents")
        self._values_by_ticker[ticker] = fair_value_cents


class PassiveQuoteStrategy:
    def __init__(self, config: StrategyConfig):
        self.config = config

    def generate_quotes(self, book: KalshiOrderBook, fair_value_cents: int, position: int, max_position: int) -> list[Quote]:
        stats = book.stats()
        buy_edge = self.config.min_edge_cents
        sell_edge = self.config.min_edge_cents

        # Inventory skew keeps the bot from blindly adding to a large position.
        # If it is already long YES, it demands a bigger edge before buying more.
        if max_position > 0:
            inventory_ratio = position / max_position
            if inventory_ratio > 0:
                buy_edge += round(inventory_ratio * self.config.inventory_skew_cents)
            elif inventory_ratio < 0:
                sell_edge += round(abs(inventory_ratio) * self.config.inventory_skew_cents)

        quotes: list[Quote] = []
        # Passive buying means bidding below fair value instead of paying the ask.
        buy_price = self._buy_price(book, fair_value_cents, buy_edge)
        if buy_price is not None and position < max_position:
            size = min(self.config.quote_size, max_position - position)
            quotes.append(
                Quote(
                    market_ticker=book.market_ticker,
                    side="buy_yes",
                    price_cents=buy_price,
                    quantity=size,
                    fair_value_cents=fair_value_cents,
                    reason=f"buy <= fair - edge ({fair_value_cents}-{buy_edge})",
                )
            )

        # Passive selling means offering above fair value instead of hitting bids.
        sell_price = self._sell_price(book, fair_value_cents, sell_edge)
        if sell_price is not None and position > -max_position:
            size = min(self.config.quote_size, max_position + position)
            quotes.append(
                Quote(
                    market_ticker=book.market_ticker,
                    side="sell_yes",
                    price_cents=sell_price,
                    quantity=size,
                    fair_value_cents=fair_value_cents,
                    reason=f"sell >= fair + edge ({fair_value_cents}+{sell_edge})",
                )
            )

        return [quote for quote in quotes if self._has_valid_edge(quote, fair_value_cents) and self._does_not_cross(quote, stats.best_yes_bid, stats.best_yes_ask)]

    def _buy_price(self, book: KalshiOrderBook, fair_value_cents: int, edge_cents: int) -> int | None:
        cap = fair_value_cents - edge_cents
        if cap < 1:
            return None
        best_bid = book.best_yes_bid()
        candidate = cap if best_bid is None else min(cap, best_bid + self.config.quote_improvement_cents)
        best_ask = book.best_yes_ask()
        if best_ask is not None and not self.config.allow_crossing:
            candidate = min(candidate, best_ask - 1)
        return candidate if 1 <= candidate <= 99 else None

    def _sell_price(self, book: KalshiOrderBook, fair_value_cents: int, edge_cents: int) -> int | None:
        floor = fair_value_cents + edge_cents
        if floor > 99:
            return None
        best_ask = book.best_yes_ask()
        candidate = floor if best_ask is None else max(floor, best_ask - self.config.quote_improvement_cents)
        best_bid = book.best_yes_bid()
        if best_bid is not None and not self.config.allow_crossing:
            candidate = max(candidate, best_bid + 1)
        return candidate if 1 <= candidate <= 99 else None

    def _has_valid_edge(self, quote: Quote, fair_value_cents: int) -> bool:
        if quote.side == "buy_yes":
            return quote.price_cents <= fair_value_cents - self.config.min_edge_cents
        return quote.price_cents >= fair_value_cents + self.config.min_edge_cents

    def _does_not_cross(self, quote: Quote, best_bid: int | None, best_ask: int | None) -> bool:
        if self.config.allow_crossing:
            return True
        if quote.side == "buy_yes" and best_ask is not None:
            return quote.price_cents < best_ask
        if quote.side == "sell_yes" and best_bid is not None:
            return quote.price_cents > best_bid
        return True
