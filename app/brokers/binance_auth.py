"""
Binance Authenticated Broker — Crypto Trading com Signed Requests.

Funcionalidades:
  - Market data em tempo real (já existia público, agora + orderbook, trades)
  - Ordens assinadas HMAC-SHA256 (compra/venda real)
  - Paper trading com dados reais + execução simulada
  - Testnet support (Binance Spot Testnet)
  - Consulta de saldo, posições, histórico de ordens

Setup:
  1. Crie conta Binance: https://www.binance.com
  2. Ative API no painel: https://www.binance.com/en/my/settings/api-management
  3. Configure env vars: BINANCE_API_KEY, BINANCE_API_SECRET

Testnet (recomendado para testes):
  - https://testnet.binance.vision
  - Configure: BINANCE_TESTNET=true

Modo Paper Trading (padrão):
  - Usa dados reais da Binance para preços
  - Simula execução de ordens localmente
  - Não envia ordens reais à exchange
"""

import asyncio
import hashlib
import hmac
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from app.core.config import settings


class BinanceAuthBroker:
    """
    Cliente Binance autenticado para dados de mercado e ordens crypto.

    Diferença do market_data.py existente:
      - market_data.py: usa API pública (só preços, klines)
      - BinanceAuthBroker: API assinada (account, orders, depth, trades)
    """

    def __init__(self):
        self.api_key = settings.BINANCE_API_KEY
        self.api_secret = settings.BINANCE_API_SECRET
        self.use_testnet = getattr(settings, "BINANCE_TESTNET", False)
        self.paper_trading = getattr(settings, "TRADING_MODE", "paper") == "paper"
        self.timeout = settings.MARKET_API_TIMEOUT

        if self.use_testnet:
            self.base_url = getattr(settings, "BINANCE_TESTNET_URL", "https://testnet.binance.vision")
        else:
            self.base_url = settings.BINANCE_BASE_URL

        self._connected = False

        # Paper trading state
        self._paper_orders: List[Dict] = []
        self._paper_balances: Dict[str, float] = {"USDT": settings.INITIAL_CAPITAL * settings.CAPITAL_USD_PCT}
        self._paper_positions: Dict[str, float] = {}  # asset → qty

        mode = "PAPER" if self.paper_trading else ("TESTNET" if self.use_testnet else "LIVE")
        has_keys = bool(self.api_key and self.api_secret)
        print(f"[binance-auth] Modo: {mode} | Keys: {'✓' if has_keys else '✗'} | URL: {self.base_url}", flush=True)

    @property
    def is_configured(self) -> bool:
        """Verifica se as credenciais Binance estão configuradas."""
        return bool(self.api_key and self.api_secret)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def status(self) -> Dict[str, Any]:
        """Status atual do broker Binance."""
        return {
            "broker": "Binance",
            "configured": self.is_configured,
            "connected": self._connected,
            "mode": "paper" if self.paper_trading else ("testnet" if self.use_testnet else "live"),
            "has_api_key": bool(self.api_key),
            "base_url": self.base_url,
            "paper_usdt": round(self._paper_balances.get("USDT", 0), 2) if self.paper_trading else None,
            "paper_orders": len(self._paper_orders) if self.paper_trading else None,
            "paper_positions": len(self._paper_positions) if self.paper_trading else None,
        }

    # ── Assinatura HMAC-SHA256 ────────────────────────────────────────────

    def _sign_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Adiciona timestamp e assinatura HMAC-SHA256 aos parâmetros."""
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    def _auth_headers(self) -> Dict[str, str]:
        """Headers com API key."""
        return {
            "X-MBX-APIKEY": self.api_key,
            "Accept": "application/json",
        }

    # ── Conexão / Health Check ────────────────────────────────────────────

    async def connect(self) -> bool:
        """Testa conexão com a API Binance (autenticada)."""
        if not self.is_configured:
            print("[binance-auth] Não configurado — usando API pública apenas", flush=True)
            return False

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Testa com /api/v3/account (requer assinatura)
                params = self._sign_params({})
                r = await client.get(
                    f"{self.base_url}/api/v3/account",
                    params=params,
                    headers=self._auth_headers(),
                )
                if r.status_code == 200:
                    data = r.json()
                    can_trade = data.get("canTrade", False)
                    self._connected = True
                    print(f"[binance-auth] Conectado! canTrade={can_trade}", flush=True)

                    # Sincroniza saldos reais (se não paper)
                    if not self.paper_trading:
                        balances = data.get("balances", [])
                        for b in balances:
                            free = float(b.get("free", 0))
                            if free > 0:
                                self._paper_balances[b["asset"]] = free
                    return True
                else:
                    self._connected = False
                    error = r.json().get("msg", r.text[:200]) if r.status_code < 500 else r.text[:200]
                    print(f"[binance-auth] Erro: {r.status_code} — {error}", flush=True)
                    return False
        except Exception as e:
            self._connected = False
            print(f"[binance-auth] Erro de conexão: {e}", flush=True)
            return False

    # ── Market Data (autenticado — mais dados que público) ────────────────

    async def get_orderbook(self, asset: str, limit: int = 20) -> Optional[Dict]:
        """Order book (livro de ofertas) — não precisa de auth, mas é extra."""
        symbol = self._to_symbol(asset)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    f"{self.base_url}/api/v3/depth",
                    params={"symbol": symbol, "limit": limit},
                )
                if r.status_code == 200:
                    data = r.json()
                    return {
                        "asset": asset.upper(),
                        "symbol": symbol,
                        "bids": [[float(p), float(q)] for p, q in data.get("bids", [])[:10]],
                        "asks": [[float(p), float(q)] for p, q in data.get("asks", [])[:10]],
                        "timestamp": datetime.now().isoformat(),
                    }
        except Exception as e:
            print(f"[binance-auth] Erro orderbook {symbol}: {e}", flush=True)
        return None

    async def get_recent_trades(self, asset: str, limit: int = 50) -> List[Dict]:
        """Trades recentes de um ativo."""
        symbol = self._to_symbol(asset)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    f"{self.base_url}/api/v3/trades",
                    params={"symbol": symbol, "limit": limit},
                )
                if r.status_code == 200:
                    return [
                        {
                            "price": float(t["price"]),
                            "qty": float(t["qty"]),
                            "time": t["time"],
                            "is_buyer_maker": t["isBuyerMaker"],
                        }
                        for t in r.json()
                    ]
        except Exception as e:
            print(f"[binance-auth] Erro trades {symbol}: {e}", flush=True)
        return []

    async def get_ticker_24h(self, asset: str) -> Optional[Dict]:
        """Estatísticas 24h com spread bid/ask."""
        symbol = self._to_symbol(asset)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    f"{self.base_url}/api/v3/ticker/24hr",
                    params={"symbol": symbol},
                )
                if r.status_code == 200:
                    d = r.json()
                    return {
                        "asset": asset.upper(),
                        "symbol": symbol,
                        "last_price": float(d.get("lastPrice", 0)),
                        "bid": float(d.get("bidPrice", 0)),
                        "ask": float(d.get("askPrice", 0)),
                        "spread": float(d.get("askPrice", 0)) - float(d.get("bidPrice", 0)),
                        "volume_24h": float(d.get("volume", 0)),
                        "quote_volume_24h": float(d.get("quoteVolume", 0)),
                        "price_change_pct": float(d.get("priceChangePercent", 0)),
                        "high_24h": float(d.get("highPrice", 0)),
                        "low_24h": float(d.get("lowPrice", 0)),
                        "trades_count": int(d.get("count", 0)),
                        "timestamp": datetime.now().isoformat(),
                        "source": "binance",
                    }
        except Exception as e:
            print(f"[binance-auth] Erro 24h {symbol}: {e}", flush=True)
        return None

    # ── Order Management ──────────────────────────────────────────────────

    async def place_order(
        self,
        asset: str,
        side: str,  # "buy" ou "sell"
        quantity: float,
        price: Optional[float] = None,
        order_type: str = "MARKET",  # "MARKET" ou "LIMIT"
    ) -> Optional[Dict]:
        """
        Coloca uma ordem na Binance.
        Em modo paper trading, simula execução localmente.
        Em modo testnet, usa endpoint de teste da Binance.
        """
        symbol = self._to_symbol(asset)
        side_upper = side.upper()

        if self.paper_trading:
            return await self._paper_place_order(asset, side_upper, quantity, price, order_type)

        if not self.is_configured:
            print(f"[binance-auth] Não configurado para ordem {side_upper} {symbol}", flush=True)
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                params = {
                    "symbol": symbol,
                    "side": side_upper,
                    "type": order_type.upper(),
                    "quantity": f"{quantity:.8f}",
                }
                if order_type.upper() == "LIMIT" and price:
                    params["timeInForce"] = "GTC"
                    params["price"] = f"{price:.8f}"

                # Se testnet, usa endpoint normal (testnet é próprio para teste)
                # Se live, usa endpoint real
                endpoint = "/api/v3/order"
                if self.use_testnet:
                    endpoint = "/api/v3/order/test"  # Testnet validation

                signed_params = self._sign_params(params)
                r = await client.post(
                    f"{self.base_url}{endpoint}",
                    params=signed_params,
                    headers=self._auth_headers(),
                )
                if r.status_code == 200:
                    result = r.json()
                    order_id = result.get("orderId", "test_ok")
                    exec_price = float(result.get("fills", [{}])[0].get("price", price or 0)) if result.get("fills") else (price or 0)
                    print(f"[binance-auth] ORDER: {side_upper} {quantity} {symbol} → ID: {order_id}", flush=True)
                    return {
                        "order_id": str(order_id),
                        "asset": asset.upper(),
                        "symbol": symbol,
                        "side": side_upper,
                        "quantity": quantity,
                        "price": exec_price,
                        "type": order_type,
                        "status": result.get("status", "FILLED"),
                        "timestamp": datetime.now().isoformat(),
                        "mode": "testnet" if self.use_testnet else "live",
                    }
                else:
                    error = r.json().get("msg", r.text[:200]) if r.status_code < 500 else r.text[:200]
                    print(f"[binance-auth] Erro ordem: {r.status_code} — {error}", flush=True)
                    return None
        except Exception as e:
            print(f"[binance-auth] Erro ordem {side_upper} {symbol}: {e}", flush=True)
            return None

    async def _paper_place_order(
        self, asset: str, side: str, quantity: float,
        price: Optional[float], order_type: str,
    ) -> Dict:
        """Simula execução na Binance (paper trading)."""
        ticker = asset.upper()
        symbol = self._to_symbol(ticker)

        # Obter preço real para simulação
        exec_price = price or 0.0
        if exec_price == 0.0:
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    r = await client.get(f"{self.base_url}/api/v3/ticker/price", params={"symbol": symbol})
                    if r.status_code == 200:
                        exec_price = float(r.json().get("price", 0))
            except Exception:
                pass
        if exec_price == 0.0:
            exec_price = 50000.0 if ticker == "BTC" else 100.0  # fallback

        order_id = f"PAPER-BN-{int(time.time() * 1000)}"
        cost_usdt = exec_price * quantity

        if side == "BUY":
            usdt_bal = self._paper_balances.get("USDT", 0)
            if cost_usdt > usdt_bal:
                print(f"[binance-auth] PAPER: Saldo insuficiente ${usdt_bal:.2f} < ${cost_usdt:.2f}", flush=True)
                return {
                    "order_id": order_id, "status": "REJECTED",
                    "reason": "insufficient_balance", "mode": "paper",
                }
            self._paper_balances["USDT"] = usdt_bal - cost_usdt
            current_qty = self._paper_positions.get(ticker, 0)
            self._paper_positions[ticker] = current_qty + quantity
        else:  # SELL
            current_qty = self._paper_positions.get(ticker, 0)
            if quantity > current_qty:
                print(f"[binance-auth] PAPER: Posição insuficiente {ticker}: {current_qty} < {quantity}", flush=True)
                return {
                    "order_id": order_id, "status": "REJECTED",
                    "reason": "insufficient_position", "mode": "paper",
                }
            self._paper_positions[ticker] = current_qty - quantity
            if self._paper_positions[ticker] <= 0:
                self._paper_positions.pop(ticker, None)
            self._paper_balances["USDT"] = self._paper_balances.get("USDT", 0) + cost_usdt

        order = {
            "order_id": order_id,
            "asset": ticker,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": exec_price,
            "type": order_type,
            "status": "FILLED",
            "cost_usdt": round(cost_usdt, 2),
            "balance_after": round(self._paper_balances.get("USDT", 0), 2),
            "timestamp": datetime.now().isoformat(),
            "mode": "paper",
        }
        self._paper_orders.append(order)
        usdt_bal = self._paper_balances.get("USDT", 0)
        print(f"[binance-auth] PAPER: {side} {quantity:.6f} {ticker} @ ${exec_price:.2f} = ${cost_usdt:.2f} | USDT: ${usdt_bal:.2f}", flush=True)
        return order

    # ── Account / Positions ───────────────────────────────────────────────

    async def get_balance(self) -> Optional[Dict]:
        """Saldo da conta Binance ou paper."""
        if self.paper_trading:
            # Calcula valor das posições em USDT
            positions_value = 0.0
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    r = await client.get(f"{self.base_url}/api/v3/ticker/price")
                    if r.status_code == 200:
                        price_map = {t["symbol"]: float(t["price"]) for t in r.json()}
                        for asset, qty in self._paper_positions.items():
                            sym = self._to_symbol(asset)
                            if sym in price_map:
                                positions_value += qty * price_map[sym]
            except Exception:
                pass

            usdt_bal = self._paper_balances.get("USDT", 0)
            return {
                "balance_usdt": round(usdt_bal, 2),
                "positions_value_usdt": round(positions_value, 2),
                "total_equity_usdt": round(usdt_bal + positions_value, 2),
                "assets": {k: round(v, 8) for k, v in self._paper_balances.items()},
                "positions": {k: round(v, 8) for k, v in self._paper_positions.items()},
                "currency": "USDT",
                "mode": "paper",
            }

        if not self.is_configured:
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                params = self._sign_params({})
                r = await client.get(
                    f"{self.base_url}/api/v3/account",
                    params=params,
                    headers=self._auth_headers(),
                )
                if r.status_code == 200:
                    data = r.json()
                    balances = {}
                    for b in data.get("balances", []):
                        free = float(b.get("free", 0))
                        locked = float(b.get("locked", 0))
                        if free > 0 or locked > 0:
                            balances[b["asset"]] = {
                                "free": free,
                                "locked": locked,
                                "total": free + locked,
                            }
                    return {
                        "balances": balances,
                        "can_trade": data.get("canTrade", False),
                        "can_withdraw": data.get("canWithdraw", False),
                        "currency": "USDT",
                        "mode": "testnet" if self.use_testnet else "live",
                    }
        except Exception as e:
            print(f"[binance-auth] Erro balance: {e}", flush=True)
        return None

    async def get_open_orders(self, asset: Optional[str] = None) -> List[Dict]:
        """Ordens abertas na Binance."""
        if self.paper_trading:
            return [o for o in self._paper_orders if o.get("status") == "NEW"]

        if not self.is_configured:
            return []

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                params = {}
                if asset:
                    params["symbol"] = self._to_symbol(asset)
                signed = self._sign_params(params)
                r = await client.get(
                    f"{self.base_url}/api/v3/openOrders",
                    params=signed,
                    headers=self._auth_headers(),
                )
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            print(f"[binance-auth] Erro open orders: {e}", flush=True)
        return []

    async def cancel_order(self, asset: str, order_id: str) -> Optional[Dict]:
        """Cancela uma ordem na Binance."""
        if self.paper_trading:
            for o in self._paper_orders:
                if o.get("order_id") == order_id and o.get("status") == "NEW":
                    o["status"] = "CANCELED"
                    return o
            return None

        if not self.is_configured:
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                params = {
                    "symbol": self._to_symbol(asset),
                    "orderId": order_id,
                }
                signed = self._sign_params(params)
                r = await client.delete(
                    f"{self.base_url}/api/v3/order",
                    params=signed,
                    headers=self._auth_headers(),
                )
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            print(f"[binance-auth] Erro cancel: {e}", flush=True)
        return None

    async def get_order_history(self, asset: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """Histórico de ordens."""
        if self.paper_trading:
            orders = self._paper_orders
            if asset:
                orders = [o for o in orders if o.get("asset") == asset.upper()]
            return orders[-limit:]

        if not self.is_configured:
            return []

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                params = {"limit": limit}
                if asset:
                    params["symbol"] = self._to_symbol(asset)
                signed = self._sign_params(params)
                r = await client.get(
                    f"{self.base_url}/api/v3/allOrders",
                    params=signed,
                    headers=self._auth_headers(),
                )
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            print(f"[binance-auth] Erro order history: {e}", flush=True)
        return []

    # ── Helpers ───────────────────────────────────────────────────────────

    def _to_symbol(self, asset: str) -> str:
        """Converte BTC → BTCUSDT para a Binance."""
        s = asset.upper()
        if s.endswith("USDT"):
            return s
        return f"{s}USDT"
