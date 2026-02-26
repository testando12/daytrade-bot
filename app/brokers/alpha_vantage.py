"""
Alpha Vantage — Dados de Mercado para US Stocks, Forex e Commodities.

Alternativa mais estável ao Yahoo Finance para dados de ações americanas.

Funcionalidades:
  - Cotações em tempo real (0.5s delay)
  - Candles intraday (1min, 5min, 15min, 30min, 60min)
  - Candles diários (até 20 anos)
  - Forex rates (EURUSD, GBPUSD, etc.)
  - Commodities (via ETFs: GLD, SLV, USO)
  - Busca de ativos (search endpoint)

Limites:
  - Free: 25 requests/dia (5 req/min)
  - Premium: 75-1200+ requests/min conforme plano

Setup:
  1. Obtenha API key grátis: https://www.alphavantage.co/support/#api-key
  2. Configure env: ALPHA_VANTAGE_KEY=sua_chave_aqui
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from app.core.config import settings


# Mapa de intervalos nossos → Alpha Vantage
_AV_INTERVAL_MAP = {
    "1m": "1min",
    "2m": "5min",   # AV não tem 2min, usa 5min
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "60m": "60min",
    "1h": "60min",
    "1d": "daily",
}

# Mapa de commodities → ETFs equivalentes na AlphaVantage
_AV_COMMODITY_MAP = {
    "GOLD": "GLD",      # SPDR Gold Trust
    "SILVER": "SLV",    # iShares Silver Trust
    "OIL": "USO",       # United States Oil Fund
    "NATGAS": "UNG",    # United States Natural Gas Fund
}

# Mapa de forex → Alpha Vantage format
_AV_FOREX_MAP = {
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"),
    "AUDUSD": ("AUD", "USD"),
    "USDCAD": ("USD", "CAD"),
    "USDCHF": ("USD", "CHF"),
    "NZDUSD": ("NZD", "USD"),
    "EURGBP": ("EUR", "GBP"),
}


class AlphaVantageBroker:
    """
    Cliente Alpha Vantage para dados de mercado.

    Prioridade de uso para US stocks:
      Alpha Vantage (mais estável) > Yahoo Finance (fallback)
    """

    def __init__(self):
        self.api_key = settings.ALPHA_VANTAGE_KEY
        self.base_url = settings.ALPHA_VANTAGE_BASE_URL
        self.timeout = settings.MARKET_API_TIMEOUT
        self._request_count = 0
        self._last_request_time = 0.0
        self._connected = False
        self._rate_limit_delay = 12.5  # 5 req/min free = 1 req cada 12s

        has_key = bool(self.api_key)
        print(f"[alpha-vantage] Key: {'✓' if has_key else '✗'} | URL: {self.base_url}", flush=True)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def status(self) -> Dict[str, Any]:
        """Status atual do Alpha Vantage."""
        return {
            "provider": "Alpha Vantage",
            "configured": self.is_configured,
            "connected": self._connected,
            "has_api_key": bool(self.api_key),
            "request_count": self._request_count,
            "rate_limit": "25/day (free) or 75+/min (premium)",
        }

    # ── Rate Limiting ─────────────────────────────────────────────────────

    async def _rate_limit(self):
        """Controle de rate limit (free: 5 req/min)."""
        import time
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - elapsed)
        self._last_request_time = time.time()
        self._request_count += 1

    # ── Conexão / Health Check ────────────────────────────────────────────

    async def connect(self) -> bool:
        """Testa conexão com Alpha Vantage."""
        if not self.is_configured:
            print("[alpha-vantage] Não configurado — Yahoo Finance como fallback para US stocks", flush=True)
            return False

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    self.base_url,
                    params={
                        "function": "GLOBAL_QUOTE",
                        "symbol": "AAPL",
                        "apikey": self.api_key,
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    if "Global Quote" in data and data["Global Quote"]:
                        self._connected = True
                        price = data["Global Quote"].get("05. price", "?")
                        print(f"[alpha-vantage] Conectado! AAPL=${price}", flush=True)
                        return True
                    elif "Note" in data:
                        print(f"[alpha-vantage] Rate limit atingido: {data['Note'][:100]}", flush=True)
                        self._connected = True  # API funciona, só rate limited
                        return True
                    elif "Error Message" in data:
                        print(f"[alpha-vantage] Erro: {data['Error Message'][:100]}", flush=True)
                        return False
                print(f"[alpha-vantage] Resposta inesperada: {r.status_code}", flush=True)
                return False
        except Exception as e:
            print(f"[alpha-vantage] Erro de conexão: {e}", flush=True)
            return False

    # ── US Stocks ─────────────────────────────────────────────────────────

    async def get_quote(self, asset: str) -> Optional[Dict[str, Any]]:
        """Cotação em tempo real de um ativo US via Alpha Vantage."""
        if not self.is_configured:
            return None

        ticker = asset.upper()
        # Se for commodity, usa ETF equivalente
        av_symbol = _AV_COMMODITY_MAP.get(ticker, ticker)

        try:
            await self._rate_limit()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    self.base_url,
                    params={
                        "function": "GLOBAL_QUOTE",
                        "symbol": av_symbol,
                        "apikey": self.api_key,
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    gq = data.get("Global Quote", {})
                    if gq:
                        price = float(gq.get("05. price", 0))
                        if price > 0:
                            return {
                                "asset": ticker,
                                "price": price,
                                "open": float(gq.get("02. open", 0)),
                                "high": float(gq.get("03. high", 0)),
                                "low": float(gq.get("04. low", 0)),
                                "volume": int(gq.get("06. volume", 0)),
                                "change_pct": float(gq.get("10. change percent", "0").replace("%", "")),
                                "prev_close": float(gq.get("08. previous close", 0)),
                                "timestamp": datetime.now().isoformat(),
                                "source": "alpha_vantage",
                            }
                    if "Note" in data:
                        print(f"[alpha-vantage] Rate limit para {ticker}", flush=True)
        except Exception as e:
            print(f"[alpha-vantage] Erro quote {ticker}: {e}", flush=True)
        return None

    async def get_quotes_batch(self, assets: List[str]) -> Dict[str, float]:
        """Cotações de múltiplos ativos (sequencial por rate limit)."""
        if not self.is_configured:
            return {}

        prices: Dict[str, float] = {}
        for asset in assets:
            quote = await self.get_quote(asset)
            if quote and quote.get("price", 0) > 0:
                prices[asset.upper()] = quote["price"]
        return prices

    async def get_candles(self, asset: str, interval: str = "5m", limit: int = 25) -> Optional[Dict]:
        """Candles intraday ou diários de um ativo US via Alpha Vantage."""
        if not self.is_configured:
            return None

        ticker = asset.upper()
        av_symbol = _AV_COMMODITY_MAP.get(ticker, ticker)
        av_interval = _AV_INTERVAL_MAP.get(interval, "5min")

        try:
            await self._rate_limit()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if av_interval == "daily":
                    params = {
                        "function": "TIME_SERIES_DAILY",
                        "symbol": av_symbol,
                        "outputsize": "compact",
                        "apikey": self.api_key,
                    }
                    time_series_key = "Time Series (Daily)"
                else:
                    params = {
                        "function": "TIME_SERIES_INTRADAY",
                        "symbol": av_symbol,
                        "interval": av_interval,
                        "outputsize": "compact",
                        "apikey": self.api_key,
                    }
                    time_series_key = f"Time Series ({av_interval})"

                r = await client.get(self.base_url, params=params)
                if r.status_code == 200:
                    data = r.json()
                    ts_data = data.get(time_series_key, {})
                    if not ts_data:
                        if "Note" in data:
                            print(f"[alpha-vantage] Rate limit candles {ticker}", flush=True)
                        return None

                    # Alpha Vantage retorna mais recente primeiro
                    sorted_dates = sorted(ts_data.keys())[-limit:]
                    prices = []
                    volumes = []
                    highs = []
                    lows = []
                    timestamps = []

                    for date_str in sorted_dates:
                        bar = ts_data[date_str]
                        prices.append(float(bar.get("4. close", 0)))
                        volumes.append(float(bar.get("5. volume", 0)))
                        highs.append(float(bar.get("2. high", 0)))
                        lows.append(float(bar.get("3. low", 0)))
                        try:
                            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S") if " " in date_str else datetime.strptime(date_str, "%Y-%m-%d")
                            timestamps.append(int(dt.timestamp()))
                        except ValueError:
                            timestamps.append(0)

                    if prices:
                        return {
                            "asset": ticker, "symbol": av_symbol, "interval": interval,
                            "prices": prices, "volumes": volumes,
                            "highs": highs, "lows": lows,
                            "timestamps": timestamps, "count": len(prices),
                            "source": "alpha_vantage",
                        }
        except Exception as e:
            print(f"[alpha-vantage] Erro candles {ticker}: {e}", flush=True)
        return None

    # ── Forex ─────────────────────────────────────────────────────────────

    async def get_forex_rate(self, pair: str) -> Optional[Dict]:
        """Taxa de câmbio em tempo real via Alpha Vantage."""
        if not self.is_configured:
            return None

        pair_upper = pair.upper()
        currencies = _AV_FOREX_MAP.get(pair_upper)
        if not currencies:
            # Tenta extrair da string diretamente (EURUSD → EUR, USD)
            if len(pair_upper) == 6:
                currencies = (pair_upper[:3], pair_upper[3:])
            else:
                return None

        from_currency, to_currency = currencies

        try:
            await self._rate_limit()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    self.base_url,
                    params={
                        "function": "CURRENCY_EXCHANGE_RATE",
                        "from_currency": from_currency,
                        "to_currency": to_currency,
                        "apikey": self.api_key,
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    rate_data = data.get("Realtime Currency Exchange Rate", {})
                    if rate_data:
                        rate = float(rate_data.get("5. Exchange Rate", 0))
                        if rate > 0:
                            return {
                                "pair": pair_upper,
                                "rate": rate,
                                "bid": float(rate_data.get("8. Bid Price", 0)),
                                "ask": float(rate_data.get("9. Ask Price", 0)),
                                "timestamp": rate_data.get("6. Last Refreshed", ""),
                                "source": "alpha_vantage",
                            }
        except Exception as e:
            print(f"[alpha-vantage] Erro forex {pair_upper}: {e}", flush=True)
        return None

    async def get_forex_candles(self, pair: str, interval: str = "5m", limit: int = 25) -> Optional[Dict]:
        """Candles forex via Alpha Vantage."""
        if not self.is_configured:
            return None

        pair_upper = pair.upper()
        currencies = _AV_FOREX_MAP.get(pair_upper)
        if not currencies:
            if len(pair_upper) == 6:
                currencies = (pair_upper[:3], pair_upper[3:])
            else:
                return None

        from_currency, to_currency = currencies
        av_interval = _AV_INTERVAL_MAP.get(interval, "5min")

        try:
            await self._rate_limit()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if av_interval == "daily":
                    params = {
                        "function": "FX_DAILY",
                        "from_symbol": from_currency,
                        "to_symbol": to_currency,
                        "outputsize": "compact",
                        "apikey": self.api_key,
                    }
                    time_series_key = "Time Series FX (Daily)"
                else:
                    params = {
                        "function": "FX_INTRADAY",
                        "from_symbol": from_currency,
                        "to_symbol": to_currency,
                        "interval": av_interval,
                        "outputsize": "compact",
                        "apikey": self.api_key,
                    }
                    time_series_key = f"Time Series FX (Intraday)"

                r = await client.get(self.base_url, params=params)
                if r.status_code == 200:
                    data = r.json()
                    ts_data = data.get(time_series_key, {})
                    if not ts_data:
                        return None

                    sorted_dates = sorted(ts_data.keys())[-limit:]
                    prices = []
                    highs = []
                    lows = []
                    timestamps = []

                    for date_str in sorted_dates:
                        bar = ts_data[date_str]
                        prices.append(float(bar.get("4. close", 0)))
                        highs.append(float(bar.get("2. high", 0)))
                        lows.append(float(bar.get("3. low", 0)))
                        try:
                            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S") if " " in date_str else datetime.strptime(date_str, "%Y-%m-%d")
                            timestamps.append(int(dt.timestamp()))
                        except ValueError:
                            timestamps.append(0)

                    if prices:
                        return {
                            "asset": pair_upper, "symbol": pair_upper, "interval": interval,
                            "prices": prices, "volumes": [0.0] * len(prices),
                            "highs": highs, "lows": lows,
                            "timestamps": timestamps, "count": len(prices),
                            "source": "alpha_vantage",
                        }
        except Exception as e:
            print(f"[alpha-vantage] Erro forex candles {pair_upper}: {e}", flush=True)
        return None

    # ── USD/BRL Rate ──────────────────────────────────────────────────────

    async def get_usd_brl_rate(self) -> Optional[float]:
        """Obtém taxa USD/BRL em tempo real."""
        result = await self.get_forex_rate("USDBRL")
        if result:
            return result["rate"]
        return None

    # ── Search ────────────────────────────────────────────────────────────

    async def search_ticker(self, keywords: str) -> List[Dict]:
        """Busca ativos por palavra-chave."""
        if not self.is_configured:
            return []

        try:
            await self._rate_limit()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    self.base_url,
                    params={
                        "function": "SYMBOL_SEARCH",
                        "keywords": keywords,
                        "apikey": self.api_key,
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    matches = data.get("bestMatches", [])
                    return [
                        {
                            "symbol": m.get("1. symbol", ""),
                            "name": m.get("2. name", ""),
                            "type": m.get("3. type", ""),
                            "region": m.get("4. region", ""),
                            "currency": m.get("8. currency", ""),
                        }
                        for m in matches
                    ]
        except Exception as e:
            print(f"[alpha-vantage] Erro search: {e}", flush=True)
        return []
