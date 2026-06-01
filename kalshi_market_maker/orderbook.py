"""Local Kalshi binary order book.

Kalshi binary books expose YES bids and NO bids. They do not directly send YES
asks. A NO bid at 35¢ is the same economic quote as a YES ask at 65¢ because
the two outcomes sum to 100¢.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable


def dollars_to_cents(value: str | int | float | Decimal) -> int:
    dollars = Decimal(str(value))
    return int((dollars * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def cents_to_dollars(cents: int) -> str:
    return f"{cents / 100:.4f}"


def fp_to_contracts(value: str | int | float | Decimal) -> float:
    return float(Decimal(str(value)))


@dataclass(frozen=True)
class BookStats:
    best_yes_bid: int | None
    best_yes_ask: int | None
    mid_price: float | None
    spread: int | None
    yes_bid_liquidity: float
    yes_ask_liquidity: float


@dataclass
class KalshiOrderBook:
    market_ticker: str
    yes_bids: dict[int, float] = field(default_factory=dict)
    no_bids: dict[int, float] = field(default_factory=dict)
    sequence: int | None = None

    @classmethod
    def from_api_response(cls, market_ticker: str, payload: dict) -> "KalshiOrderBook":
        raw_book = payload.get("orderbook_fp", payload.get("orderbook", payload))
        book = cls(market_ticker=market_ticker)
        book.replace_levels(raw_book.get("yes_dollars", raw_book.get("yes", [])), "yes")
        book.replace_levels(raw_book.get("no_dollars", raw_book.get("no", [])), "no")
        return book

    @classmethod
    def from_ws_snapshot(cls, payload: dict) -> "KalshiOrderBook":
        msg = payload.get("msg", payload)
        book = cls(market_ticker=str(msg["market_ticker"]), sequence=payload.get("seq"))
        book.replace_levels(msg.get("yes_dollars_fp", msg.get("yes_dollars", [])), "yes")
        book.replace_levels(msg.get("no_dollars_fp", msg.get("no_dollars", [])), "no")
        return book

    def replace_levels(self, levels: Iterable[Iterable[object]], side: str) -> None:
        target = self._side_map(side)
        target.clear()
        for level in levels:
            price, quantity = self._parse_level(level)
            if quantity > 0:
                target[price] = quantity

    def apply_delta(self, payload: dict) -> None:
        msg = payload.get("msg", payload)
        self.sequence = payload.get("seq", self.sequence)
        if msg.get("market_ticker") and msg["market_ticker"] != self.market_ticker:
            raise ValueError(f"Delta ticker {msg['market_ticker']} does not match {self.market_ticker}")

        if "yes_dollars_fp" in msg or "no_dollars_fp" in msg:
            for level in msg.get("yes_dollars_fp", []):
                self._set_level("yes", *self._parse_level(level))
            for level in msg.get("no_dollars_fp", []):
                self._set_level("no", *self._parse_level(level))
            return

        side = str(msg.get("side", msg.get("book_side", ""))).lower()
        if side in {"bid", "yes"}:
            normalized_side = "yes"
        elif side in {"ask", "no"}:
            normalized_side = "no"
        else:
            raise ValueError(f"Unknown order book delta side: {side!r}")

        price_value = msg.get("price_dollars", msg.get("price", msg.get("yes_price_dollars", msg.get("no_price_dollars"))))
        if price_value is None:
            raise ValueError("Order book delta is missing a price")
        price = dollars_to_cents(price_value) if isinstance(price_value, str) or float(price_value) <= 1 else int(price_value)

        if "delta" in msg:
            current = self._side_map(normalized_side).get(price, 0)
            quantity = current + fp_to_contracts(msg["delta"])
        else:
            quantity_value = msg.get("count_fp", msg.get("quantity", msg.get("count", 0)))
            quantity = fp_to_contracts(quantity_value)
        self._set_level(normalized_side, price, quantity)

    def best_yes_bid(self) -> int | None:
        return max((price for price, quantity in self.yes_bids.items() if quantity > 0), default=None)

    def best_yes_ask(self) -> int | None:
        # Kalshi sends NO bids, not YES asks. Buying NO at 35¢ means another
        # trader is willing to sell YES at 65¢, so the implied YES ask is 100-35.
        no_bid = max((price for price, quantity in self.no_bids.items() if quantity > 0), default=None)
        if no_bid is None:
            return None
        return 100 - no_bid

    def liquidity_at_yes_bid(self, price: int) -> float:
        return self.yes_bids.get(price, 0)

    def liquidity_at_yes_ask(self, yes_ask_price: int) -> float:
        return self.no_bids.get(100 - yes_ask_price, 0)

    def stats(self) -> BookStats:
        best_bid = self.best_yes_bid()
        best_ask = self.best_yes_ask()
        spread = None
        mid = None
        if best_bid is not None and best_ask is not None:
            spread = best_ask - best_bid
            mid = (best_bid + best_ask) / 2
        return BookStats(
            best_yes_bid=best_bid,
            best_yes_ask=best_ask,
            mid_price=mid,
            spread=spread,
            yes_bid_liquidity=self.liquidity_at_yes_bid(best_bid) if best_bid is not None else 0,
            yes_ask_liquidity=self.liquidity_at_yes_ask(best_ask) if best_ask is not None else 0,
        )

    def _side_map(self, side: str) -> dict[int, float]:
        normalized = side.lower()
        if normalized == "yes":
            return self.yes_bids
        if normalized == "no":
            return self.no_bids
        raise ValueError(f"Unknown book side: {side!r}")

    def _set_level(self, side: str, price: int, quantity: float) -> None:
        target = self._side_map(side)
        if quantity <= 0:
            target.pop(price, None)
        else:
            target[price] = quantity

    @staticmethod
    def _parse_level(level: Iterable[object]) -> tuple[int, float]:
        values = list(level)
        if len(values) != 2:
            raise ValueError(f"Price level must have [price, quantity], got {values!r}")
        price_raw, quantity_raw = values
        price = dollars_to_cents(price_raw)
        quantity = fp_to_contracts(quantity_raw)
        return price, quantity
