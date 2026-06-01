"""Risk checks that run before any quote reaches a broker."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .config import MarketConfig, RiskConfig
from .orderbook import KalshiOrderBook
from .strategy import Quote


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str


class RiskManager:
    def __init__(self, config: RiskConfig):
        self.config = config

    def kill_switch_active(self) -> bool:
        return os.getenv(self.config.kill_switch_path, "").strip().lower() in {"1", "true", "yes", "on"}

    def check_quote(
        self,
        quote: Quote,
        book: KalshiOrderBook,
        market: MarketConfig,
        position: int,
        open_order_count: int,
        daily_pnl_cents: int,
    ) -> RiskDecision:
        if self.kill_switch_active():
            return RiskDecision(False, "kill switch active")
        if self.config.cancel_before_major_news and market.before_major_news:
            return RiskDecision(False, "major news or settlement risk flag active")
        if daily_pnl_cents <= -self.config.max_total_daily_loss_cents:
            return RiskDecision(False, "daily loss limit reached")
        if open_order_count >= self.config.max_open_orders:
            return RiskDecision(False, "open order limit reached")
        if quote.quantity > self.config.max_contracts_per_order:
            return RiskDecision(False, "order size exceeds max contracts per order")

        max_position = min(market.max_position, self.config.max_position_per_market)
        projected_position = position + quote.quantity if quote.side == "buy_yes" else position - quote.quantity
        if abs(projected_position) > max_position:
            return RiskDecision(False, "market position limit would be exceeded")

        spread = book.stats().spread
        if spread is not None and spread < self.config.min_spread_cents:
            return RiskDecision(False, "market spread is too tight")

        return RiskDecision(True, "risk checks passed")

    def should_cancel_existing_order(
        self,
        book: KalshiOrderBook,
        market: MarketConfig,
        old_fair_value_cents: int,
        new_fair_value_cents: int,
    ) -> RiskDecision:
        if self.kill_switch_active():
            return RiskDecision(True, "kill switch active")
        if self.config.cancel_before_major_news and market.before_major_news:
            return RiskDecision(True, "major news or settlement risk flag active")
        spread = book.stats().spread
        if spread is not None and spread < self.config.min_spread_cents:
            return RiskDecision(True, "market spread is too tight")
        if old_fair_value_cents != new_fair_value_cents:
            return RiskDecision(True, "fair value changed")
        return RiskDecision(False, "order can remain open")
