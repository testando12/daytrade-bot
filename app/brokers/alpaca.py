"""
Alpaca Markets Broker — US Stocks + ETFs (paper e live).

Funcionalidades:
  - Market data real-time / histórico (REST)
  - Execução de ordens (paper e live)
  - Consulta de saldo e posições

Setup:
  1. Crie conta gratuita em: https://alpaca.markets
  2. Painel → API Keys → gere um par (Paper ou Live)
  3. Configure env vars:
       ALPACA_API_KEY=PKxxxxxxxxxxxxxxxx
       ALPACA_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
       ALPACA_PAPER=True   (mude para False em conta live)

Conta Paper (padrão):
  - Mercado real, ordens simuladas com US$100.000 virtuais
  - Sem risco de capital real, ideal para validar estratégia
  - URL: https://paper-api.alpaca.markets

Conta Live:
  - Ordens reais enviadas para NYSE/NASDAQ
  - Exige depósito mínimo (US$0 — sem mínimo na Alpaca)
  - URL: https://api.alpaca.markets
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from app.core.config import settings


class AlpacaBroker:
    """
    Cliente Alpaca Markets para US stocks e ETFs.

    Prioridade no bot:
      Alpaca (auth) → Alpha Vantage → Yahoo Finance
    """

    _PAPER_URL = "https://paper-api.alpaca.markets"
    _LIVE_URL  = "https://api.alpaca.markets"
    _DATA_URL  = "https://data.alpaca.markets"

    # Timeframe Alpaca → bot interno
    _TF_MAP = {
        "1m": "1Min", "2m": "2Min", "5m": "5Min",
        "15m": "15Min", "30m": "30Min",
        "60m": "1Hour", "1h": "1Hour", "1d": "1Day",
    }

    def __init__(self):
        self.api_key    = settings.ALPACA_API_KEY
        self.api_secret = settings.ALPACA_API_SECRET
        self.paper      = getattr(settings, "ALPACA_PAPER", True)
        self.timeout    = settings.MARKET_API_TIMEOUT

        self._base_url = self._PAPER_URL if self.paper else self._LIVE_URL
        self._connected = False

        # Paper state local (espelho do paper Alpaca + fallback offline)
        self._paper_orders: List[Dict] = []
        self._paper_balance: float = 100_000.0  # US$ 100k padrão Alpaca paper

        mode = "PAPER" if self.paper else "LIVE"
        print(
            f"[alpaca] Modo: {mode} | Keys: {'✓' if self.is_configured else '✗'} | "
            f"URL: {self._base_url}",
            flush=True,
        )

    # ── Propriedades ──────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID":     self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Accept":              "application/json",
        }

    def status(self) -> Dict[str, Any]:
        return {
            "broker":      "Alpaca Markets",
            "configured":  self.is_configured,
            "connected":   self._connected,
            "mode":        "paper" if self.paper else "live",
            "base_url":    self._base_url,
            "has_api_key": bool(self.api_key),
        }

    # ── Conexão ───────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Testa conectividade e autenticação via /v2/account."""
        if not self.is_configured:
            return False
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    f"{self._base_url}/v2/account",
                    headers=self._headers(),
                )
                if r.status_code == 200:
                    data = r.json()
                    self._connected = True
                    self._paper_balance = float(data.get("cash", self._paper_balance))
                    print(
                        f"[alpaca] Conectado ✓ | Cash: US${self._paper_balance:,.2f} | "
                        f"Status: {data.get('status', '?')}",
                        flush=True,
                    )
                    return True
                else:
                    print(f"[alpaca] Falha auth: {r.status_code} {r.text[:200]}", flush=True)
                    return False
        except Exception as e:
            print(f"[alpaca] Erro conexão: {e}", flush=True)
            return False

    # ── Market Data ───────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Optional[Dict]:
        """Última cotação de um símbolo (ask/bid/last price)."""
        if not self.is_configured:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    f"{self._DATA_URL}/v2/stocks/{symbol}/quotes/latest",
                    headers=self._headers(),
                )
                if r.status_code == 200:
                    q = r.json().get("quote", {})
                    # Alpaca retorna ask/bid; estimamos mid-price
                    ask = float(q.get("ap", 0))
                    bid = float(q.get("bp", 0))
                    price = (ask + bid) / 2 if ask and bid else ask or bid
                    return {
                        "symbol": symbol,
                        "price":  round(price, 4),
                        "ask":    ask,
                        "bid":    bid,
                        "source": "alpaca",
                    }
        except Exception as e:
            print(f"[alpaca] get_quote {symbol}: {e}", flush=True)
        return None

    async def get_quotes_batch(self, symbols: List[str]) -> Dict[str, float]:
        """Cotações em lote — retorna {symbol: price}. Máximo 100 por chamada."""
        if not self.is_configured or not symbols:
            return {}
        results: Dict[str, float] = {}
        # Alpaca suporta até 100 símbolos por chamada
        chunk_size = 100
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                for i in range(0, len(symbols), chunk_size):
                    chunk = symbols[i: i + chunk_size]
                    r = await client.get(
                        f"{self._DATA_URL}/v2/stocks/quotes/latest",
                        headers=self._headers(),
                        params={"symbols": ",".join(chunk)},
                    )
                    if r.status_code == 200:
                        for sym, q in r.json().get("quotes", {}).items():
                            ask = float(q.get("ap", 0))
                            bid = float(q.get("bp", 0))
                            price = (ask + bid) / 2 if ask and bid else ask or bid
                            if price > 0:
                                results[sym] = round(price, 4)
        except Exception as e:
            print(f"[alpaca] get_quotes_batch: {e}", flush=True)
        return results

    async def get_candles(
        self,
        symbol: str,
        interval: str = "1d",
        limit: int = 100,
    ) -> Optional[List[Dict]]:
        """Histórico de candles OHLCV."""
        if not self.is_configured:
            return None
        tf = self._TF_MAP.get(interval, "1Day")
        try:
            # Calcula start date baseado no limit e timeframe
            now = datetime.now(timezone.utc)
            if "Day" in tf:
                start = now - timedelta(days=limit + 10)
            elif "Hour" in tf:
                start = now - timedelta(hours=limit + 5)
            else:
                start = now - timedelta(minutes=int(tf.replace("Min", "")) * limit + 60)

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    f"{self._DATA_URL}/v2/stocks/{symbol}/bars",
                    headers=self._headers(),
                    params={
                        "timeframe": tf,
                        "start":     start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "limit":     limit,
                        "adjustment": "all",
                    },
                )
                if r.status_code == 200:
                    bars = r.json().get("bars", [])
                    return [
                        {
                            "timestamp": b["t"],
                            "open":      float(b["o"]),
                            "high":      float(b["h"]),
                            "low":       float(b["l"]),
                            "close":     float(b["c"]),
                            "volume":    float(b["v"]),
                        }
                        for b in bars
                    ]
        except Exception as e:
            print(f"[alpaca] get_candles {symbol}: {e}", flush=True)
        return None

    # ── Ordens ────────────────────────────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        side: str,        # "buy" | "sell"
        qty: float,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> Optional[Dict]:
        """Envia ordem à Alpaca (paper ou live)."""
        if not self.is_configured:
            return None

        payload: Dict[str, Any] = {
            "symbol":       symbol,
            "qty":          str(round(qty, 6)),
            "side":         side.lower(),
            "type":         order_type,
            "time_in_force": "day",
        }
        if order_type == "limit" and limit_price:
            payload["limit_price"] = str(round(limit_price, 4))
        if order_type in ("stop", "stop_limit") and stop_price:
            payload["stop_price"] = str(round(stop_price, 4))

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self._base_url}/v2/orders",
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json=payload,
                )
                if r.status_code in (200, 201):
                    order = r.json()
                    print(
                        f"[alpaca] Ordem {side.upper()} {qty} {symbol} → "
                        f"id={order.get('id','?')[:8]} status={order.get('status','?')}",
                        flush=True,
                    )
                    return order
                else:
                    print(f"[alpaca] Erro ordem: {r.status_code} {r.text[:300]}", flush=True)
        except Exception as e:
            print(f"[alpaca] place_order {symbol}: {e}", flush=True)
        return None

    async def get_positions(self) -> List[Dict]:
        """Lista posições abertas na conta Alpaca."""
        if not self.is_configured:
            return []
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    f"{self._base_url}/v2/positions",
                    headers=self._headers(),
                )
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            print(f"[alpaca] get_positions: {e}", flush=True)
        return []

    async def get_account(self) -> Optional[Dict]:
        """Retorna informações da conta (saldo, buying power, etc.)."""
        if not self.is_configured:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    f"{self._base_url}/v2/account",
                    headers=self._headers(),
                )
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            print(f"[alpaca] get_account: {e}", flush=True)
        return None

    async def get_order_history(self, limit: int = 50) -> List[Dict]:
        """Histórico de ordens."""
        if not self.is_configured:
            return []
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    f"{self._base_url}/v2/orders",
                    headers=self._headers(),
                    params={"status": "all", "limit": limit, "direction": "desc"},
                )
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            print(f"[alpaca] get_order_history: {e}", flush=True)
        return []

    async def cancel_all_orders(self) -> bool:
        """Cancela todas as ordens abertas."""
        if not self.is_configured:
            return False
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.delete(
                    f"{self._base_url}/v2/orders",
                    headers=self._headers(),
                )
                return r.status_code in (200, 204, 207)
        except Exception as e:
            print(f"[alpaca] cancel_all_orders: {e}", flush=True)
        return False
