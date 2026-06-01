# Kalshi Market Maker

Educational, paper-first Kalshi market-making bot for binary YES/NO prediction markets.

This is not a profitability claim. It is a risk-controlled learning tool that starts in paper trading and logs simulated activity until live trading is explicitly enabled in two places.

## Safety defaults

- `paper_trading` defaults to `true`.
- Live trading requires both:
  - command-line flag: `--live`
  - config confirmation: `"paper_trading": false` and `"live_trading_confirmed": true`
- API keys are read from environment variables only.
- Orders are submitted with post-only/resting-order intent where Kalshi supports it.
- Set `KALSHI_KILL_SWITCH=true` to force risk checks to reject new quotes.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`cryptography` is needed for authenticated request signing. `websockets` is needed for market-data and user-fill streams. Paper-mode unit tests use only the standard library.

## Configure

Copy the example config and edit tickers/fair values:

```bash
cp kalshi_market_maker/example_config.json kalshi_config.json
```

Important fields:

- `markets[].ticker` - Kalshi market ticker.
- `markets[].fair_value_cents` - manual YES fair value, 1-99.
- `markets[].seed_yes_bids` / `seed_no_bids` - optional offline paper-mode book levels when API credentials are not present.
- `strategy.min_edge_cents` - minimum required edge from fair value.
- `risk.max_position_per_market` - hard market-level position cap.
- `risk.max_total_daily_loss_cents` - paper/live P&L stop.
- `risk.max_open_orders` - max resting orders allowed.

## Paper dry run

```bash
python3 -m kalshi_market_maker.main --config kalshi_config.json --once
```

The bot will:

1. Load book data from Kalshi if credentials are present, otherwise use seed levels from config.
2. Convert Kalshi YES/NO bids into best YES bid and implied YES ask.
3. Generate passive buy/sell quotes around manual fair value.
4. Run risk checks.
5. Place paper orders only.
6. Log JSON lines to `logs/kalshi_bot.jsonl`.

## API credentials

Never commit keys. Use environment variables:

```bash
export KALSHI_API_KEY_ID="your-key-id"
export KALSHI_PRIVATE_KEY_PATH="/secure/path/to/kalshi_private.key"
```

or:

```bash
export KALSHI_PRIVATE_KEY="$(cat /secure/path/to/kalshi_private.key)"
```

## Live trading

Live trading is disabled unless all gates are opened:

```json
{
  "paper_trading": false,
  "live_trading_confirmed": true
}
```

Then run:

```bash
python3 -m kalshi_market_maker.main --config kalshi_config.json --once --live
```

Start with Kalshi demo credentials. Review logs and positions manually before using production credentials.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Code map

- `config.py` - config dataclasses and JSON loader.
- `kalshi_client.py` - REST signing, order book fetches, order placement, cancellation, WebSocket subscription helper.
- `orderbook.py` - local YES/NO book and YES ask conversion.
- `strategy.py` - manual fair values and passive quote generation.
- `risk.py` - position, loss, order-count, spread, news, and kill-switch checks.
- `paper_broker.py` - conservative simulated fills and paper P&L.
- `live_broker.py` - live-order safety gates.
- `main.py` - safe CLI entrypoint.
