"""Configuration helpers for the paper-first Kalshi bot."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MarketConfig:
    ticker: str
    fair_value_cents: int
    max_position: int
    before_major_news: bool = False
    seed_yes_bids: list[list[str | int | float]] = field(default_factory=list)
    seed_no_bids: list[list[str | int | float]] = field(default_factory=list)


@dataclass(frozen=True)
class RiskConfig:
    max_position_per_market: int = 20
    max_total_daily_loss_cents: int = 2_000
    max_open_orders: int = 10
    max_contracts_per_order: int = 5
    min_spread_cents: int = 2
    cancel_before_major_news: bool = True
    kill_switch_path: str = "KALSHI_KILL_SWITCH"


@dataclass(frozen=True)
class StrategyConfig:
    min_edge_cents: int = 2
    quote_size: int = 1
    quote_improvement_cents: int = 1
    inventory_skew_cents: int = 2
    allow_crossing: bool = False
    cancel_on_fair_value_change_cents: int = 1


@dataclass(frozen=True)
class KalshiConfig:
    paper_trading: bool = True
    live_trading_confirmed: bool = False
    demo: bool = True
    api_key_id_env: str = "KALSHI_API_KEY_ID"
    private_key_env: str = "KALSHI_PRIVATE_KEY"
    private_key_path_env: str = "KALSHI_PRIVATE_KEY_PATH"
    log_path: str = "logs/kalshi_bot.jsonl"
    fee_cents_per_contract: int = 1
    markets: list[MarketConfig] = field(default_factory=list)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)

    @property
    def api_key_id(self) -> str | None:
        return os.getenv(self.api_key_id_env)

    @property
    def private_key_pem(self) -> str | None:
        direct_value = os.getenv(self.private_key_env)
        if direct_value:
            return direct_value
        key_path = os.getenv(self.private_key_path_env)
        if key_path:
            return Path(key_path).read_text(encoding="utf-8")
        return None

    @property
    def has_api_credentials(self) -> bool:
        return bool(self.api_key_id and self.private_key_pem)


def _read_bool(raw: object, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(raw)


def _read_market(raw: dict[str, object]) -> MarketConfig:
    ticker = str(raw["ticker"])
    seed_yes_bids = raw.get("seed_yes_bids", [])
    seed_no_bids = raw.get("seed_no_bids", [])
    return MarketConfig(
        ticker=ticker,
        fair_value_cents=int(raw["fair_value_cents"]),
        max_position=int(raw.get("max_position", 20)),
        before_major_news=_read_bool(raw.get("before_major_news"), False),
        seed_yes_bids=list(seed_yes_bids) if isinstance(seed_yes_bids, list) else [],
        seed_no_bids=list(seed_no_bids) if isinstance(seed_no_bids, list) else [],
    )


def load_config(path: str | Path) -> KalshiConfig:
    raw_loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw_loaded, dict):
        raise ValueError("Config file must contain a JSON object")
    raw: dict[str, object] = raw_loaded
    risk_value = raw.get("risk", {})
    strategy_value = raw.get("strategy", {})
    markets_value = raw.get("markets", [])
    risk_raw = risk_value if isinstance(risk_value, dict) else {}
    strategy_raw = strategy_value if isinstance(strategy_value, dict) else {}
    markets_raw = markets_value if isinstance(markets_value, list) else []
    return KalshiConfig(
        paper_trading=_read_bool(raw.get("paper_trading"), True),
        live_trading_confirmed=_read_bool(raw.get("live_trading_confirmed"), False),
        demo=_read_bool(raw.get("demo"), True),
        api_key_id_env=str(raw.get("api_key_id_env", "KALSHI_API_KEY_ID")),
        private_key_env=str(raw.get("private_key_env", "KALSHI_PRIVATE_KEY")),
        private_key_path_env=str(raw.get("private_key_path_env", "KALSHI_PRIVATE_KEY_PATH")),
        log_path=str(raw.get("log_path", "logs/kalshi_bot.jsonl")),
        fee_cents_per_contract=int(raw.get("fee_cents_per_contract", 1)),
        markets=[_read_market(market) for market in markets_raw if isinstance(market, dict)],
        risk=RiskConfig(
            max_position_per_market=int(risk_raw.get("max_position_per_market", 20)),
            max_total_daily_loss_cents=int(risk_raw.get("max_total_daily_loss_cents", 2_000)),
            max_open_orders=int(risk_raw.get("max_open_orders", 10)),
            max_contracts_per_order=int(risk_raw.get("max_contracts_per_order", 5)),
            min_spread_cents=int(risk_raw.get("min_spread_cents", 2)),
            cancel_before_major_news=_read_bool(risk_raw.get("cancel_before_major_news"), True),
            kill_switch_path=str(risk_raw.get("kill_switch_path", "KALSHI_KILL_SWITCH")),
        ),
        strategy=StrategyConfig(
            min_edge_cents=int(strategy_raw.get("min_edge_cents", 2)),
            quote_size=int(strategy_raw.get("quote_size", 1)),
            quote_improvement_cents=int(strategy_raw.get("quote_improvement_cents", 1)),
            inventory_skew_cents=int(strategy_raw.get("inventory_skew_cents", 2)),
            allow_crossing=_read_bool(strategy_raw.get("allow_crossing"), False),
            cancel_on_fair_value_change_cents=int(strategy_raw.get("cancel_on_fair_value_change_cents", 1)),
        ),
    )
