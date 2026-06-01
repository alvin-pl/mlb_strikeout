"""Paper broker with conservative fill simulation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .orderbook import KalshiOrderBook
from .strategy import Quote


@dataclass
class PaperOrder:
    order_id: str
    quote: Quote
    remaining_quantity: int
    created_ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class PaperFill:
    order_id: str
    market_ticker: str
    side: str
    price_cents: int
    quantity: int
    fee_cents: int


class PaperBroker:
    def __init__(self, fee_cents_per_contract: int = 1):
        self.fee_cents_per_contract = fee_cents_per_contract
        self.orders: dict[str, PaperOrder] = {}
        self.positions: dict[str, int] = {}
        self.cash_cents = 0
        self._next_order_id = 1

    def place_order(self, quote: Quote) -> PaperOrder:
        order = PaperOrder(
            order_id=f"paper-{self._next_order_id}",
            quote=quote,
            remaining_quantity=quote.quantity,
        )
        self._next_order_id += 1
        self.orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> PaperOrder | None:
        return self.orders.pop(order_id, None)

    def cancel_all(self) -> list[PaperOrder]:
        canceled = list(self.orders.values())
        self.orders.clear()
        return canceled

    def open_order_count(self) -> int:
        return len(self.orders)

    def position(self, ticker: str) -> int:
        return self.positions.get(ticker, 0)

    def simulate_fills(self, books: dict[str, KalshiOrderBook]) -> list[PaperFill]:
        fills: list[PaperFill] = []
        for order_id, order in list(self.orders.items()):
            book = books.get(order.quote.market_ticker)
            if book is None:
                continue
            if self._should_fill(order.quote, book):
                fill = self._fill_order(order)
                fills.append(fill)
                self.orders.pop(order_id, None)
        return fills

    def mark_to_market_pnl_cents(self, books: dict[str, KalshiOrderBook]) -> int:
        value = self.cash_cents
        for ticker, position in self.positions.items():
            mid = books[ticker].stats().mid_price if ticker in books else None
            if mid is not None:
                value += round(position * mid)
        return value

    def _should_fill(self, quote: Quote, book: KalshiOrderBook) -> bool:
        stats = book.stats()
        if quote.side == "buy_yes":
            return stats.best_yes_ask is not None and stats.best_yes_ask <= quote.price_cents
        return stats.best_yes_bid is not None and stats.best_yes_bid >= quote.price_cents

    def _fill_order(self, order: PaperOrder) -> PaperFill:
        quote = order.quote
        quantity = order.remaining_quantity
        fee = quantity * self.fee_cents_per_contract
        current_position = self.positions.get(quote.market_ticker, 0)
        if quote.side == "buy_yes":
            self.positions[quote.market_ticker] = current_position + quantity
            self.cash_cents -= quote.price_cents * quantity + fee
        else:
            self.positions[quote.market_ticker] = current_position - quantity
            self.cash_cents += quote.price_cents * quantity - fee
        return PaperFill(
            order_id=order.order_id,
            market_ticker=quote.market_ticker,
            side=quote.side,
            price_cents=quote.price_cents,
            quantity=quantity,
            fee_cents=fee,
        )
