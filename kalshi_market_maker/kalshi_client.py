"""Small Kalshi REST/WebSocket client.

The paper broker can run without credentials. Live REST/WebSocket methods require
`KALSHI_API_KEY_ID` and either `KALSHI_PRIVATE_KEY` or `KALSHI_PRIVATE_KEY_PATH`.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import AsyncIterator

from .config import KalshiConfig
from .orderbook import cents_to_dollars
from .strategy import Quote


class KalshiClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class KalshiEndpoints:
    rest_base_url: str
    ws_url: str


def endpoints_for_config(config: KalshiConfig) -> KalshiEndpoints:
    if config.demo:
        return KalshiEndpoints(
            rest_base_url="https://external-api.demo.kalshi.co/trade-api/v2",
            ws_url="wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2",
        )
    return KalshiEndpoints(
        rest_base_url="https://external-api.kalshi.com/trade-api/v2",
        ws_url="wss://external-api-ws.kalshi.com/trade-api/ws/v2",
    )


class KalshiClient:
    def __init__(self, config: KalshiConfig):
        self.config = config
        self.endpoints = endpoints_for_config(config)

    def get_market(self, ticker: str) -> dict[str, object]:
        return self._request("GET", f"/markets/{urllib.parse.quote(ticker)}")

    def get_orderbook(self, ticker: str, depth: int = 0) -> dict[str, object]:
        query = f"?depth={depth}" if depth else ""
        return self._request("GET", f"/markets/{urllib.parse.quote(ticker)}/orderbook{query}")

    def get_open_orders(self, ticker: str | None = None) -> dict[str, object]:
        query = "?status=resting"
        if ticker:
            query += f"&ticker={urllib.parse.quote(ticker)}"
        return self._request("GET", f"/portfolio/orders{query}")

    def create_yes_order(self, quote: Quote) -> dict[str, object]:
        payload = {
            "ticker": quote.market_ticker,
            "client_order_id": f"edu-mm-{int(time.time() * 1000)}",
            "side": "yes",
            "action": "buy" if quote.side == "buy_yes" else "sell",
            "count": quote.quantity,
            "yes_price": quote.price_cents,
            "time_in_force": "good_till_canceled",
            "post_only": True,
            "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True,
        }
        return self._request("POST", "/portfolio/orders", payload)

    def create_book_order_v2(self, quote: Quote) -> dict[str, object]:
        payload = {
            "ticker": quote.market_ticker,
            "client_order_id": f"edu-mm-{int(time.time() * 1000)}",
            "side": "bid" if quote.side == "buy_yes" else "ask",
            "count": f"{quote.quantity:.2f}",
            "price": cents_to_dollars(quote.price_cents),
            "time_in_force": "good_till_canceled",
            "post_only": True,
            "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True,
        }
        return self._request("POST", "/portfolio/events/orders", payload)

    def cancel_order(self, order_id: str) -> dict[str, object]:
        return self._request("DELETE", f"/portfolio/orders/{urllib.parse.quote(order_id)}")

    def batch_cancel_orders(self, order_ids: list[str]) -> dict[str, object]:
        payload = {"orders": [{"order_id": order_id} for order_id in order_ids]}
        return self._request("DELETE", "/portfolio/orders/batched", payload)

    def websocket_headers(self) -> dict[str, str]:
        return self._auth_headers("GET", "/trade-api/ws/v2")

    async def stream_market_data(self, market_tickers: list[str], include_user_fills: bool) -> AsyncIterator[dict[str, object]]:
        try:
            import websockets
        except ImportError as exc:
            raise KalshiClientError("Install websockets to use WebSocket streaming: pip install websockets") from exc

        channels = ["orderbook_delta"]
        if include_user_fills:
            channels.append("fill")

        async with websockets.connect(self.endpoints.ws_url, additional_headers=self.websocket_headers()) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {"channels": channels, "market_tickers": market_tickers},
                    }
                )
            )
            async for raw_message in websocket:
                data = json.loads(raw_message)
                if isinstance(data, dict):
                    yield data

    def _request(self, method: str, path_with_query: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        self._require_credentials()
        path = path_with_query.split("?", 1)[0]
        url = f"{self.endpoints.rest_base_url}{path_with_query}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Content-Type": "application/json",
            **self._auth_headers(method, f"/trade-api/v2{path}"),
        }
        request = urllib.request.Request(url=url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise KalshiClientError(f"Kalshi API {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise KalshiClientError(f"Kalshi API connection failed: {exc}") from exc
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            raise KalshiClientError("Kalshi API returned a non-object response")
        return data

    def _require_credentials(self) -> None:
        if not self.config.has_api_credentials:
            raise KalshiClientError(
                "Missing Kalshi API credentials. Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY or KALSHI_PRIVATE_KEY_PATH."
            )

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        self._require_credentials()
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method.upper()}{path}"
        signature = self._sign_message(message)
        return {
            "KALSHI-ACCESS-KEY": self.config.api_key_id or "",
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": signature,
        }

    def _sign_message(self, message: str) -> str:
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError as exc:
            raise KalshiClientError("Install cryptography to sign live Kalshi requests: pip install cryptography") from exc

        private_key_pem = self.config.private_key_pem
        if private_key_pem is None:
            raise KalshiClientError("Missing private key")
        private_key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
        signature = private_key.sign(
            message.encode("utf-8"),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")
