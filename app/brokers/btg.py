"""
BTG Pactual Broker — Market Data + Order Management para B3.

Funcionalidades:
  - Market data real-time (cotações B3 via API BTG)
  - Histórico de candles (intraday + diário)
  - Gestão de ordens (paper trading ou ordens reais)
  - Consulta de saldo e posições

Setup:
  1. Crie conta BTG Pactual Digital: https://www.btgpactual.com
  2. Ative API no painel do investidor
  3. Configure env vars: BTG_API_KEY, BTG_API_SECRET, BTG_ACCOUNT_ID

Modo Paper Trading (padrão):
  - Usa dados reais da BTG para cotações
  - Simula execução de ordens localmente
  - Não envia ordens reais à B3
"""

import asyncio
import hashlib
import hmac
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from app.core.config import settings


class BTGBroker:
    """
    Cliente BTG Pactual para dados de mercado e ordens na B3.

    Hierarquia de fontes para B3:
      BTG (real-time com conta) > BRAPI (delay 15-30min) > Yahoo (fallback)
    """

    def __init__(self):
        self.api_key = settings.BTG_API_KEY
        self.api_secret = settings.BTG_API_SECRET
        self.account_id = settings.BTG_ACCOUNT_ID
        self.base_url = settings.BTG_BASE_URL.rstrip("/")
        self.paper_trading = settings.BTG_PAPER_TRADING
        self.timeout = settings.MARKET_API_TIMEOUT
        self._token: Optional[str] = None
        self._token_expiry: float = 0
        self._connected = False

        # Paper trading state (defaults)
        self._paper_orders: List[Dict] = []
        self._paper_positions: Dict[str, Dict] = {}
        self._paper_balance: float = settings.INITIAL_CAPITAL * settings.CAPITAL_BRL_PCT

        # ── Restaurar estado persistido ────────────────────────────────
        try:
            from app import db_state as _dbs
            _saved = _dbs.load_state("paper_btg", {})
            if _saved and "balance" in _saved:
                self._paper_orders = _saved.get("orders", [])[-100:]
                self._paper_positions = _saved.get("positions", {})
                self._paper_balance = _saved.get("balance", self._paper_balance)
                print(f"[btg] Paper state restored: R${self._paper_balance:.2f} | {len(self._paper_positions)} positions | {len(self._paper_orders)} orders", flush=True)
        except Exception as e:
            print(f"[btg] Aviso: não restaurou paper state: {e}", flush=True)

        mode = "PAPER" if self.paper_trading else "LIVE"
        has_keys = bool(self.api_key and self.api_secret)
        print(f"[btg] Modo: {mode} | Keys: {'✓' if has_keys else '✗'} | Account: {self.account_id or 'N/A'}", flush=True)

    @property
    def is_configured(self) -> bool:
        """Verifica se as credenciais BTG estão configuradas."""
        return bool(self.api_key and self.api_secret)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def status(self) -> Dict[str, Any]:
        """Status atual do broker BTG."""
        return {
            "broker": "BTG Pactual",
            "configured": self.is_configured,
            "connected": self._connected,
            "mode": "paper" if self.paper_trading else "live",
            "account_id": self.account_id or None,
            "has_api_key": bool(self.api_key),
            "base_url": self.base_url,
            "paper_balance": round(self._paper_balance, 2) if self.paper_trading else None,
            "paper_orders": len(self._paper_orders) if self.paper_trading else None,
            "paper_positions": len(self._paper_positions) if self.paper_trading else None,
        }

    # ── Autenticação ──────────────────────────────────────────────────────

    def _sign_request(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        """Gera headers de autenticação HMAC-SHA256 para a API BTG."""
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method.upper()}{path}{body}"
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return {
            "X-API-KEY": self.api_key,
            "X-TIMESTAMP": timestamp,
            "X-SIGNATURE": signature,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _get_auth_token(self, client: httpx.AsyncClient) -> Optional[str]:
        """Obtém token OAuth2 via client_credentials."""
        if not self.is_configured:
            return None

        # Se token ainda é válido, reutiliza
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        try:
            r = await client.post(
                f"{self.base_url}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.api_key,
                    "client_secret": self.api_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code == 200:
                data = r.json()
                self._token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                self._token_expiry = time.time() + expires_in
                self._connected = True
                print(f"[btg] Token obtido com sucesso (expira em {expires_in}s)", flush=True)
                return self._token
            else:
                print(f"[btg] Erro ao obter token: {r.status_code} {r.text[:200]}", flush=True)
                return None
        except Exception as e:
            print(f"[btg] Erro OAuth: {e}", flush=True)
            return None

    def _auth_headers(self, token: str) -> Dict[str, str]:
        """Headers com Bearer token."""
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── Conexão / Health Check ────────────────────────────────────────────

    async def connect(self) -> bool:
        """Testa conexão com a API BTG."""
        if not self.is_configured:
            print("[btg] Não configurado — operando sem BTG (BRAPI/Yahoo como fallback)", flush=True)
            return False

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                token = await self._get_auth_token(client)
                if token:
                    self._connected = True
                    print("[btg] Conectado com sucesso à API BTG Pactual", flush=True)
                    return True
                else:
                    self._connected = False
                    print("[btg] Falha na autenticação", flush=True)
                    return False
        except Exception as e:
            self._connected = False
            print(f"[btg] Erro de conexão: {e}", flush=True)
            return False

    # ── Market Data ───────────────────────────────────────────────────────

    async def get_quote(self, asset: str) -> Optional[Dict[str, Any]]:
        """Cotação real-time de um ativo B3 via BTG."""
        if not self.is_configured:
            return None

        ticker = asset.upper()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                token = await self._get_auth_token(client)
                if not token:
                    return None

                r = await client.get(
                    f"{self.base_url}/market-data/v1/quotes/{ticker}",
                    headers=self._auth_headers(token),
                )
                if r.status_code == 200:
                    data = r.json()
                    price = data.get("lastPrice") or data.get("price") or data.get("regularMarketPrice")
                    if price is not None:
                        return {
                            "asset": ticker,
                            "price": float(price),
                            "bid": float(data.get("bid", 0) or 0),
                            "ask": float(data.get("ask", 0) or 0),
                            "volume": int(data.get("volume", 0) or 0),
                            "change_pct": float(data.get("changePercent", 0) or data.get("priceChangePercent", 0) or 0),
                            "timestamp": datetime.now().isoformat(),
                            "source": "btg",
                        }
                elif r.status_code == 401:
                    self._token = None  # Force re-auth
                    print(f"[btg] Token expirado para {ticker}", flush=True)
                else:
                    print(f"[btg] Erro quote {ticker}: {r.status_code}", flush=True)
        except Exception as e:
            print(f"[btg] Erro quote {ticker}: {e}", flush=True)
        return None

    async def get_quotes_batch(self, assets: List[str]) -> Dict[str, float]:
        """Cotações de múltiplos ativos B3 via BTG."""
        if not self.is_configured:
            return {}

        prices: Dict[str, float] = {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                token = await self._get_auth_token(client)
                if not token:
                    return {}

                symbols = ",".join(a.upper() for a in assets)
                r = await client.get(
                    f"{self.base_url}/market-data/v1/quotes",
                    params={"symbols": symbols},
                    headers=self._auth_headers(token),
                )
                if r.status_code == 200:
                    data = r.json()
                    quotes = data if isinstance(data, list) else data.get("quotes", data.get("results", []))
                    for q in quotes:
                        sym = q.get("symbol", "").upper()
                        price = q.get("lastPrice") or q.get("price") or q.get("regularMarketPrice")
                        if sym and price is not None:
                            prices[sym] = float(price)
                    if prices:
                        print(f"[btg] {len(prices)} cotações obtidas", flush=True)
                elif r.status_code == 401:
                    self._token = None
        except Exception as e:
            print(f"[btg] Erro batch quotes: {e}", flush=True)
        return prices

    async def get_candles(self, asset: str, interval: str = "5m", limit: int = 25) -> Optional[Dict]:
        """Candles históricos de um ativo B3 via BTG."""
        if not self.is_configured:
            return None

        ticker = asset.upper()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                token = await self._get_auth_token(client)
                if not token:
                    return None

                r = await client.get(
                    f"{self.base_url}/market-data/v1/candles/{ticker}",
                    params={"interval": interval, "limit": limit},
                    headers=self._auth_headers(token),
                )
                if r.status_code == 200:
                    data = r.json()
                    candles = data if isinstance(data, list) else data.get("candles", data.get("data", []))
                    if not candles:
                        return None

                    candles = candles[-limit:]
                    prices = [float(c.get("close", 0)) for c in candles]
                    volumes = [float(c.get("volume", 0) or 0) for c in candles]
                    highs = [float(c.get("high", 0) or 0) for c in candles]
                    lows = [float(c.get("low", 0) or 0) for c in candles]
                    timestamps = [int(c.get("timestamp", 0) or 0) for c in candles]

                    return {
                        "asset": ticker, "symbol": ticker, "interval": interval,
                        "prices": prices, "volumes": volumes,
                        "highs": highs, "lows": lows,
                        "timestamps": timestamps, "count": len(prices),
                        "source": "btg",
                    }
        except Exception as e:
            print(f"[btg] Erro candles {ticker}: {e}", flush=True)
        return None

    # ── Order Management ──────────────────────────────────────────────────

    async def place_order(
        self,
        asset: str,
        side: str,  # "buy" ou "sell"
        quantity: float,
        price: Optional[float] = None,  # None = market order
        order_type: str = "market",  # "market" ou "limit"
    ) -> Optional[Dict]:
        """
        Coloca uma ordem na B3 via BTG.
        Em modo paper trading, simula a execução localmente.
        """
        ticker = asset.upper()
        side = side.lower()

        if self.paper_trading:
            return await self._paper_place_order(ticker, side, quantity, price, order_type)

        # LIVE ORDER — envia à BTG
        if not self.is_configured:
            print(f"[btg] LIVE: Não configurado para enviar ordem {side} {ticker}", flush=True)
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                token = await self._get_auth_token(client)
                if not token:
                    return None

                order_data = {
                    "symbol": ticker,
                    "side": side.upper(),
                    "type": order_type.upper(),
                    "quantity": quantity,
                    "accountId": self.account_id,
                }
                if price and order_type == "limit":
                    order_data["price"] = price

                r = await client.post(
                    f"{self.base_url}/trading/v1/orders",
                    json=order_data,
                    headers=self._auth_headers(token),
                )
                if r.status_code in (200, 201):
                    result = r.json()
                    order_id = result.get("orderId", result.get("id", "unknown"))
                    print(f"[btg] LIVE ORDER: {side} {quantity} {ticker} → ID: {order_id}", flush=True)
                    return {
                        "order_id": order_id,
                        "asset": ticker,
                        "side": side,
                        "quantity": quantity,
                        "price": price,
                        "type": order_type,
                        "status": result.get("status", "submitted"),
                        "timestamp": datetime.now().isoformat(),
                        "mode": "live",
                    }
                else:
                    print(f"[btg] Erro ordem {side} {ticker}: {r.status_code} {r.text[:200]}", flush=True)
                    return None
        except Exception as e:
            print(f"[btg] Erro ordem {side} {ticker}: {e}", flush=True)
            return None

    async def _paper_place_order(
        self, ticker: str, side: str, quantity: float,
        price: Optional[float], order_type: str,
    ) -> Dict:
        """Simula execução de ordem (paper trading)."""
        # Usa o preço fornecido ou tenta obter preço real
        exec_price = price or 0.0
        if exec_price == 0.0:
            quote = await self.get_quote(ticker)
            if quote:
                exec_price = quote["price"]
            else:
                exec_price = 100.0  # fallback para simulação

        order_id = f"PAPER-BTG-{int(time.time() * 1000)}"
        cost = exec_price * quantity

        if side == "buy":
            if cost > self._paper_balance:
                print(f"[btg] PAPER: Saldo insuficiente R${self._paper_balance:.2f} < R${cost:.2f}", flush=True)
                return {
                    "order_id": order_id, "status": "rejected",
                    "reason": "insufficient_balance", "mode": "paper",
                }
            self._paper_balance -= cost
            pos = self._paper_positions.get(ticker, {"qty": 0.0, "avg_price": 0.0})
            total_qty = pos["qty"] + quantity
            pos["avg_price"] = (pos["avg_price"] * pos["qty"] + exec_price * quantity) / total_qty if total_qty > 0 else 0
            pos["qty"] = total_qty
            self._paper_positions[ticker] = pos
        else:
            pos = self._paper_positions.get(ticker, {"qty": 0.0, "avg_price": 0.0})
            if quantity > pos["qty"]:
                print(f"[btg] PAPER: Posição insuficiente {ticker}: {pos['qty']} < {quantity}", flush=True)
                return {
                    "order_id": order_id, "status": "rejected",
                    "reason": "insufficient_position", "mode": "paper",
                }
            self._paper_balance += cost
            pos["qty"] -= quantity
            if pos["qty"] <= 0:
                self._paper_positions.pop(ticker, None)
            else:
                self._paper_positions[ticker] = pos

        order = {
            "order_id": order_id,
            "asset": ticker,
            "side": side,
            "quantity": quantity,
            "price": exec_price,
            "type": order_type,
            "status": "filled",
            "cost": round(cost, 2),
            "balance_after": round(self._paper_balance, 2),
            "timestamp": datetime.now().isoformat(),
            "mode": "paper",
        }
        self._paper_orders.append(order)
        self._save_paper_state()
        print(f"[btg] PAPER: {side.upper()} {quantity} {ticker} @ R${exec_price:.2f} = R${cost:.2f} | Saldo: R${self._paper_balance:.2f}", flush=True)
        return order

    def _save_paper_state(self):
        """Persiste estado do paper trading no banco/arquivo."""
        try:
            from app import db_state as _dbs
            _dbs.save_state("paper_btg", {
                "orders": self._paper_orders[-100:],
                "positions": dict(self._paper_positions),
                "balance": self._paper_balance,
                "updated_at": datetime.now().isoformat(),
            })
        except Exception as e:
            print(f"[btg] Erro ao salvar paper state: {e}", flush=True)

    # ── Account / Positions ───────────────────────────────────────────────

    async def get_balance(self) -> Optional[Dict]:
        """Saldo da conta BTG ou paper."""
        if self.paper_trading:
            positions_value = sum(
                p["qty"] * p["avg_price"] for p in self._paper_positions.values()
            )
            return {
                "balance": round(self._paper_balance, 2),
                "positions_value": round(positions_value, 2),
                "total_equity": round(self._paper_balance + positions_value, 2),
                "currency": "BRL",
                "mode": "paper",
            }

        if not self.is_configured:
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                token = await self._get_auth_token(client)
                if not token:
                    return None

                r = await client.get(
                    f"{self.base_url}/accounts/v1/{self.account_id}/balance",
                    headers=self._auth_headers(token),
                )
                if r.status_code == 200:
                    data = r.json()
                    return {
                        "balance": float(data.get("availableBalance", data.get("balance", 0))),
                        "positions_value": float(data.get("positionsValue", 0)),
                        "total_equity": float(data.get("totalEquity", data.get("equity", 0))),
                        "currency": "BRL",
                        "mode": "live",
                    }
        except Exception as e:
            print(f"[btg] Erro balance: {e}", flush=True)
        return None

    async def get_positions(self) -> List[Dict]:
        """Posições abertas na BTG ou paper."""
        if self.paper_trading:
            return [
                {
                    "asset": ticker,
                    "quantity": pos["qty"],
                    "avg_price": round(pos["avg_price"], 2),
                    "mode": "paper",
                }
                for ticker, pos in self._paper_positions.items()
                if pos["qty"] > 0
            ]

        if not self.is_configured:
            return []

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                token = await self._get_auth_token(client)
                if not token:
                    return []

                r = await client.get(
                    f"{self.base_url}/accounts/v1/{self.account_id}/positions",
                    headers=self._auth_headers(token),
                )
                if r.status_code == 200:
                    data = r.json()
                    positions = data if isinstance(data, list) else data.get("positions", [])
                    return [
                        {
                            "asset": p.get("symbol", ""),
                            "quantity": float(p.get("quantity", 0)),
                            "avg_price": float(p.get("avgPrice", 0)),
                            "mode": "live",
                        }
                        for p in positions
                    ]
        except Exception as e:
            print(f"[btg] Erro positions: {e}", flush=True)
        return []

    async def get_order_history(self, limit: int = 50) -> List[Dict]:
        """Histórico de ordens."""
        if self.paper_trading:
            return self._paper_orders[-limit:]

        if not self.is_configured:
            return []

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                token = await self._get_auth_token(client)
                if not token:
                    return []

                r = await client.get(
                    f"{self.base_url}/trading/v1/orders",
                    params={"accountId": self.account_id, "limit": limit},
                    headers=self._auth_headers(token),
                )
                if r.status_code == 200:
                    data = r.json()
                    return data if isinstance(data, list) else data.get("orders", [])
        except Exception as e:
            print(f"[btg] Erro order history: {e}", flush=True)
        return []
