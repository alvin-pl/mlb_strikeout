"""Live broker wrapper with two explicit safety gates."""

from __future__ import annotations

from .config import KalshiConfig
from .kalshi_client import KalshiClient
from .strategy import Quote


class LiveTradingNotEnabled(RuntimeError):
    pass


class LiveBroker:
    def __init__(self, config: KalshiConfig, client: KalshiClient, cli_live_flag: bool):
        if config.paper_trading:
            raise LiveTradingNotEnabled("Config still has paper_trading=true")
        if not cli_live_flag:
            raise LiveTradingNotEnabled("Start with --live to enable live trading")
        if not config.live_trading_confirmed:
            raise LiveTradingNotEnabled("Set live_trading_confirmed=true in config")
        self.client = client

    def place_order(self, quote: Quote) -> dict:
        return self.client.create_yes_order(quote)

    def cancel_order(self, order_id: str) -> dict:
        return self.client.cancel_order(order_id)

    def cancel_all(self, order_ids: list[str]) -> dict:
        if not order_ids:
            return {"orders": []}
        return self.client.batch_cancel_orders(order_ids)
