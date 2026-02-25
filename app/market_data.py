"""
Servico de dados de mercado — multi-fonte com menor latência gratuita

Fontes de dados:
  1. Binance API  — Crypto (TEMPO REAL, gratuito, sem chave, BTCUSDT...)
  2. BRAPI        — B3 (primário: delay ~15min com token grátis ou ~30min sem token)
     Obtenha token grátis em: https://brapi.dev/dashboard
  3. Yahoo Finance — US stocks + fallback geral (delay ~1min durante pregão)

Configure o token via variável de ambiente:
  set BRAPI_TOKEN=seu_token_aqui
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


# ── BRAPI ────────────────────────────────────────────────────────────────────
_BRAPI_BASE    = "https://brapi.dev/api"
_BRAPI_HEADERS = {"Accept": "application/json"}

# Ativos suportados gratuitamente sem token (plano free da BRAPI)
_BRAPI_FREE_SYMBOLS = {"PETR4", "MGLU3", "VALE3", "ITUB4"}

# BRAPI interval → range
_BRAPI_RANGE = {
    "1m":  "1d",  "2m":  "1d",  "5m":  "1d",
    "15m": "5d",  "30m": "1mo", "60m": "1mo",
    "1h":  "1mo", "1d":  "6mo",
}

# ── Binance (crypto — tempo real, sem auth) ──────────────────────────────────
_BINANCE_BASE    = "https://api.binance.com/api/v3"
_BINANCE_HEADERS = {"Accept": "application/json"}
# Intervalos Binance: 1m 3m 5m 15m 30m 1h 4h 1d 1w
_BINANCE_INTERVAL_MAP = {
    "1m": "1m", "2m": "3m", "5m": "5m",
    "15m": "15m", "30m": "30m", "60m": "1h",
    "1h": "1h", "1d": "1d",
}

# ── Yahoo Finance (US stocks + fallback) ──────────────────────────────────────
_YF_BASE    = "https://query1.finance.yahoo.com/v8/finance/chart"
_YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
_YF_RANGE = {
    "1m": "1d", "2m": "5d", "5m": "5d",
    "15m": "5d", "30m": "1mo", "60m": "1mo",
    "1h": "1mo", "1d": "6mo",
}

_CRYPTO_SYMBOLS = {
    "BTC","ETH","BNB","SOL","ADA","XRP","DOGE",
    "DOT","AVAX","MATIC","LINK","LTC","UNI","ATOM","TRX"
}

# US stocks set — populated at import time from settings (lazy to avoid circular import)
_US_STOCK_SYMBOLS: set = set()


class BrapiMarketData:
    """
    Cliente multi-fonte de dados de mercado.
    - Crypto:    Binance (tempo real) → fallback Yahoo
    - B3:        BRAPI (token grátis) → fallback Yahoo
    - US Stocks: Yahoo Finance
    """

    VALID_INTERVALS = ["1m", "2m", "5m", "15m", "30m", "60m", "1h", "1d"]

    def __init__(self):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx nao instalado. Execute: pip install httpx")
        self.timeout = getattr(settings, "MARKET_API_TIMEOUT", 8)
        self._semaphore = asyncio.Semaphore(30)  # max 30 concurrent requests
        self.token   = getattr(settings, "BRAPI_TOKEN", "").strip()
        # Populate US stock set from settings
        global _US_STOCK_SYMBOLS
        _US_STOCK_SYMBOLS = {s.upper() for s in getattr(settings, "US_STOCKS", [])}
        brapi_src = "BRAPI+token" if self.token else "BRAPI free (4 ativos)"
        print(f"[market] Crypto=Binance(RT) | B3={brapi_src}>Yahoo | US=Yahoo", flush=True)
        print(f"[market] US stocks carregadas: {len(_US_STOCK_SYMBOLS)}", flush=True)

    # ── helpers de símbolo ────────────────────────────────────────────────────

    def _is_crypto(self, asset: str) -> bool:
        return asset.upper() in _CRYPTO_SYMBOLS

    def _is_us_stock(self, asset: str) -> bool:
        return asset.upper() in _US_STOCK_SYMBOLS

    def _yf_symbol(self, asset: str) -> str:
        s = asset.upper()
        if s.endswith(".SA") or s.endswith("-USD"):
            return s
        if self._is_crypto(s):
            return f"{s}-USD"
        if self._is_us_stock(s):
            return s          # US stocks: AAPL, MSFT — no suffix
        return f"{s}.SA"      # B3: PETR4 → PETR4.SA

    def _brapi_supported(self, asset: str) -> bool:
        """BRAPI suporta o ativo? (com token: só B3; sem token: só free set)"""
        s = asset.upper()
        if self._is_crypto(s):
            return False  # BRAPI foca em B3; criptos vão para Yahoo
        if self._is_us_stock(s):
            return False  # US stocks via Yahoo Finance direto
        return bool(self.token) or s in _BRAPI_FREE_SYMBOLS

    def _brapi_params(self, extra: dict = None) -> dict:
        params = {}
        if self.token:
            params["token"] = self.token
        if extra:
            params.update(extra)
        return params

    # ── BRAPI: preço atual (batch) ────────────────────────────────────────────

    async def _brapi_get_prices(self, client: httpx.AsyncClient, assets: List[str]) -> Dict[str, float]:
        """Busca preços atuais de vários ativos via BRAPI em uma requisição."""
        assets_upper = [a.upper() for a in assets]
        symbols = ",".join(assets_upper)
        try:
            r = await client.get(
                f"{_BRAPI_BASE}/quote/{symbols}",
                params=self._brapi_params(),
                headers=_BRAPI_HEADERS,
            )
            if r.status_code != 200:
                return {}
            results = r.json().get("results", [])
            prices: Dict[str, float] = {}
            # Map each result back to the requested symbol (by exact match first,
            # then by position) so BRAPI symbol drift doesn't pollute our keys.
            requested_set = set(assets_upper)
            matched = set()
            for item in results:
                brapi_sym = item.get("symbol", "")
                price     = item.get("regularMarketPrice")
                if price is None:
                    continue
                if brapi_sym in requested_set:
                    prices[brapi_sym] = float(price)
                    matched.add(brapi_sym)
            # For anything BRAPI returned with a drifted symbol, match by order
            unmatched_req = [a for a in assets_upper if a not in matched]
            extra_items   = [it for it in results if it.get("symbol", "") not in requested_set
                             and it.get("regularMarketPrice") is not None]
            for req, item in zip(unmatched_req, extra_items):
                prices[req] = float(item["regularMarketPrice"])
            return prices
        except Exception as e:
            print(f"[brapi] Erro ao buscar preços {symbols}: {e}", flush=True)
            return {}

    # ── BRAPI: klines (histórico) ─────────────────────────────────────────────

    async def _brapi_get_klines(self, client: httpx.AsyncClient, asset: str, interval: str, limit: int) -> Optional[Dict]:
        """Busca candles históricos de um ativo via BRAPI."""
        ticker = asset.upper()
        brapi_range = _BRAPI_RANGE.get(interval, "1d")
        try:
            r = await client.get(
                f"{_BRAPI_BASE}/quote/{ticker}",
                params=self._brapi_params({
                    "range":                 brapi_range,
                    "interval":              interval,
                    "fundamental":           "false",
                    "dividends":             "false",
                }),
                headers=_BRAPI_HEADERS,
            )
            if r.status_code != 200:
                return None

            results = r.json().get("results", [])
            if not results:
                return None

            item   = results[0]
            hist   = item.get("historicalDataPrice", [])
            if not hist:
                # sem histórico — pelo menos retorna preço atual como 1 ponto
                price = item.get("regularMarketPrice")
                if price:
                    return {
                        "asset": ticker, "symbol": ticker, "interval": interval,
                        "prices": [float(price)], "volumes": [0.0],
                        "highs":  [float(price)], "lows":    [float(price)],
                        "timestamps": [0], "count": 1, "source": "brapi",
                    }
                return None

            hist = hist[-limit:]
            # Filtrar entradas válidas primeiro
            valid_hist = [h for h in hist if h.get("close") is not None]
            if not valid_hist:
                return None
            prices  = [float(h["close"]) for h in valid_hist]
            volumes = [float(h.get("volume", 0) or 0) for h in valid_hist]
            highs   = [float(h.get("high",  h["close"]) or h["close"]) for h in valid_hist]
            lows    = [float(h.get("low",   h["close"]) or h["close"]) for h in valid_hist]
            ts      = [int(h.get("date", 0)) for h in valid_hist]

            return {
                "asset": ticker, "symbol": ticker, "interval": interval,
                "prices": prices, "volumes": volumes,
                "highs":  highs,  "lows":    lows,
                "timestamps": ts, "count": len(prices), "source": "brapi",
            }
        except Exception as e:
            print(f"[brapi] Erro ao buscar klines {ticker}: {e}", flush=True)
            return None

    # ── Binance (crypto — tempo real) ────────────────────────────────────────

    def _binance_symbol(self, asset: str) -> str:
        """Converte BTC → BTCUSDT para a Binance."""
        s = asset.upper()
        if s.endswith("USDT"):
            return s
        if s.endswith("BTC") and len(s) > 3:
            return s  # BTC pair like ETHBTC
        return f"{s}USDT"

    async def _binance_get_prices(self, client: httpx.AsyncClient, assets: List[str]) -> Dict[str, float]:
        """Preços em tempo real da Binance para uma lista de crypto."""
        prices: Dict[str, float] = {}
        try:
            # Obtém todos os preços de uma vez (payload ~80 KB, muito rápido)
            r = await client.get(
                f"{_BINANCE_BASE}/ticker/price",
                headers=_BINANCE_HEADERS,
            )
            if r.status_code != 200:
                return prices
            ticker_map = {item["symbol"]: float(item["price"])
                          for item in r.json()}
            for asset in assets:
                sym = self._binance_symbol(asset)
                if sym in ticker_map:
                    prices[asset.upper()] = ticker_map[sym]
                else:
                    print(f"[binance] {sym} nao encontrado", flush=True)
        except Exception as e:
            print(f"[binance] Erro precos: {e}", flush=True)
        return prices

    async def _binance_get_klines(self, client: httpx.AsyncClient, asset: str, interval: str, limit: int) -> Optional[Dict]:
        """Candles em tempo real da Binance para crypto."""
        ticker = self._binance_symbol(asset)
        binance_interval = _BINANCE_INTERVAL_MAP.get(interval, "5m")
        try:
            r = await client.get(
                f"{_BINANCE_BASE}/klines",
                params={"symbol": ticker, "interval": binance_interval, "limit": limit},
                headers=_BINANCE_HEADERS,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            if not data:
                return None
            # Binance kline: [openTime, open, high, low, close, volume, closeTime, ...]
            prices  = [float(k[4]) for k in data]  # close
            volumes = [float(k[5]) for k in data]
            highs   = [float(k[2]) for k in data]
            lows    = [float(k[3]) for k in data]
            ts      = [int(k[0]) // 1000 for k in data]
            sym     = asset.upper()
            return {
                "asset": sym, "symbol": sym, "interval": interval,
                "prices": prices, "volumes": volumes,
                "highs":  highs,  "lows":    lows,
                "timestamps": ts, "count": len(prices), "source": "binance",
            }
        except Exception as e:
            print(f"[binance] Erro klines {ticker}: {e}", flush=True)
        return None

    # ── Yahoo Finance (US stocks + fallback) ─────────────────────────────────

    async def _yf_get_price(self, client: httpx.AsyncClient, asset: str) -> Optional[float]:
        try:
            r = await client.get(
                f"{_YF_BASE}/{self._yf_symbol(asset)}",
                params={"interval": "1m", "range": "1d"},
                headers=_YF_HEADERS,
            )
            if r.status_code == 200:
                price = r.json()["chart"]["result"][0]["meta"].get("regularMarketPrice")
                return float(price) if price is not None else None
        except Exception as e:
            print(f"[yahoo] Erro preco {asset}: {e}", flush=True)
        return None

    async def _yf_get_klines(self, client: httpx.AsyncClient, asset: str, interval: str, limit: int) -> Optional[Dict]:
        ticker   = asset.upper()
        yf_range = _YF_RANGE.get(interval, "5d")
        try:
            r = await client.get(
                f"{_YF_BASE}/{self._yf_symbol(ticker)}",
                params={"interval": interval, "range": yf_range},
                headers=_YF_HEADERS,
            )
            if r.status_code != 200:
                return None
            chart_result = r.json()["chart"]["result"]
            if not chart_result:
                return None
            res    = chart_result[0]
            ts_raw = res.get("timestamp", [])
            q      = res.get("indicators", {}).get("quote", [{}])[0]
            closes = q.get("close",  [])
            vols   = q.get("volume", [])
            highs  = q.get("high",   [])
            lows   = q.get("low",    [])
            valid  = [i for i in range(len(closes)) if closes[i] is not None][-limit:]
            if not valid:
                return None
            return {
                "asset": ticker, "symbol": ticker, "interval": interval,
                "prices":  [float(closes[i]) for i in valid],
                "volumes": [float(vols[i])   if vols  and vols[i]  is not None else 0.0 for i in valid],
                "highs":   [float(highs[i])  if highs and highs[i] is not None else 0.0 for i in valid],
                "lows":    [float(lows[i])   if lows  and lows[i]  is not None else 0.0 for i in valid],
                "timestamps": [ts_raw[i] if i < len(ts_raw) else 0 for i in valid],
                "count": len(valid), "source": "yahoo",
            }
        except Exception as e:
            print(f"[yahoo] Erro klines {ticker}: {e}", flush=True)
        return None

    # ── Interface pública ─────────────────────────────────────────────────────

    async def get_current_price(self, asset: str) -> Optional[Dict]:
        """Preço atual de um ativo. Binance (crypto) | BRAPI (B3) | Yahoo (US/fallback)."""
        ticker = asset.upper()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            price = None
            if self._is_crypto(ticker):
                got = await self._binance_get_prices(client, [ticker])
                price = got.get(ticker)
                if price:
                    print(f"[binance] {ticker}: $ {price}", flush=True)
            elif self._brapi_supported(ticker):
                prices = await self._brapi_get_prices(client, [ticker])
                price  = prices.get(ticker)
                if price:
                    print(f"[brapi] {ticker}: R$ {price}", flush=True)
            if price is None:
                price = await self._yf_get_price(client, ticker)
                if price:
                    print(f"[yahoo] {ticker}: {price}", flush=True)
            if price is not None:
                return {
                    "asset": ticker, "symbol": ticker,
                    "price": price, "timestamp": datetime.now().isoformat(),
                }
        return None

    async def get_all_prices(self, assets: Optional[List[str]] = None) -> Dict[str, float]:
        """
        Preços atuais de múltiplos ativos.
        - Crypto  → Binance (tempo real, 1 request batch)
        - B3      → BRAPI → fallback Yahoo
        - US/rest → Yahoo
        """
        if assets is None:
            assets = settings.ALLOWED_ASSETS

        prices: Dict[str, float] = {}
        crypto_assets = [a for a in assets if self._is_crypto(a)]
        brapi_assets  = [a for a in assets if self._brapi_supported(a)]
        yf_assets     = [a for a in assets if not self._is_crypto(a) and not self._brapi_supported(a)]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # Binance: 1 request para todos os cryptos (tempo real)
            if crypto_assets:
                got = await self._binance_get_prices(client, crypto_assets)
                prices.update(got)
                # Fallback Yahoo para cryptos que a Binance não retornou
                missing = [a for a in crypto_assets if a.upper() not in prices]
                for a in missing:
                    p = await self._yf_get_price(client, a)
                    if p:
                        prices[a.upper()] = p

            # BRAPI batch para B3 (até 20 por vez com token)
            if brapi_assets:
                if self.token:
                    for i in range(0, len(brapi_assets), 20):
                        chunk = brapi_assets[i:i+20]
                        got   = await self._brapi_get_prices(client, chunk)
                        prices.update(got)
                        failed = [a for a in chunk if a.upper() not in prices]
                        for a in failed:
                            p = await self._yf_get_price(client, a)
                            if p:
                                prices[a.upper()] = p
                else:
                    # Free: chamadas individuais (só 4 símbolos gratuitos)
                    tasks = [self._brapi_get_prices(client, [a]) for a in brapi_assets]
                    for a, result in zip(brapi_assets, await asyncio.gather(*tasks, return_exceptions=True)):
                        if isinstance(result, dict) and result:
                            prices.update(result)
                        else:
                            p = await self._yf_get_price(client, a)
                            if p:
                                prices[a.upper()] = p

            # Yahoo para US stocks e qualquer ativo não coberto
            if yf_assets:
                results = await asyncio.gather(
                    *[self._yf_get_price(client, a) for a in yf_assets],
                    return_exceptions=True,
                )
                for a, p in zip(yf_assets, results):
                    if isinstance(p, float):
                        prices[a.upper()] = p

        return prices

    async def get_klines(
        self,
        asset: str,
        interval: str = "5m",
        limit: int = 25,
        _client: Optional[Any] = None,
    ) -> Optional[Dict]:
        """Candles historicos. Binance (crypto RT) | BRAPI (B3) | Yahoo (US/fallback)."""
        ticker = asset.upper()
        if interval not in self.VALID_INTERVALS:
            interval = "5m"
        async with self._semaphore:
            if _client is not None:
                client = _client
            else:
                client = httpx.AsyncClient(timeout=self.timeout)
            try:
                result = None
                if self._is_crypto(ticker):
                    result = await self._binance_get_klines(client, ticker, interval, limit)
                elif self._brapi_supported(ticker):
                    result = await self._brapi_get_klines(client, ticker, interval, limit)
                if result is None:
                    result = await self._yf_get_klines(client, ticker, interval, limit)
                return result
            finally:
                if _client is None:
                    await client.aclose()

    async def get_all_klines(
        self,
        assets: Optional[List[str]] = None,
        interval: str = "5m",
        limit: int = 25,
        timeout: float = 45.0,
    ) -> Dict[str, Dict]:
        """Klines de multiplos ativos. Usa asyncio.wait para coletar resultados parciais.
        Compartilha um unico httpx.AsyncClient para reduzir overhead de conexao."""
        if assets is None:
            assets = settings.ALLOWED_ASSETS

        async with httpx.AsyncClient(timeout=self.timeout) as shared_client:
            task_map = {
                asyncio.create_task(self.get_klines(a, interval, limit, _client=shared_client)): a
                for a in assets
            }

            done, pending = await asyncio.wait(task_map.keys(), timeout=timeout)

            # Cancel tasks still running after timeout
            for t in pending:
                t.cancel()
            if pending:
                print(f"[market] get_all_klines: {len(done)}/{len(assets)} OK, {len(pending)} timeout ({timeout}s, {interval})", flush=True)
            else:
                print(f"[market] get_all_klines: {len(done)}/{len(assets)} OK ({interval})", flush=True)

        market_data: Dict[str, Dict] = {}
        for t in done:
            asset = task_map[t]
            try:
                klines = t.result()
                if isinstance(klines, dict) and klines and klines.get("count", 0) > 0:
                    market_data[asset.upper()] = {
                        "prices":  klines["prices"],
                        "volumes": klines["volumes"],
                    }
            except Exception:
                pass
        return market_data

    async def get_24h_ticker(self, asset: str) -> Optional[Dict]:
        """Estatísticas de 24h. Binance (crypto) | BRAPI (B3) | Yahoo (US/fallback)."""
        ticker = asset.upper()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # Binance: ticker 24h em tempo real para crypto
            if self._is_crypto(ticker):
                try:
                    r = await client.get(
                        f"{_BINANCE_BASE}/ticker/24hr",
                        params={"symbol": self._binance_symbol(ticker)},
                        headers=_BINANCE_HEADERS,
                    )
                    if r.status_code == 200:
                        d = r.json()
                        return {
                            "asset": ticker, "symbol": ticker,
                            "last_price":       float(d.get("lastPrice", 0)),
                            "price_change_pct": round(float(d.get("priceChangePercent", 0)), 2),
                            "volume":           float(d.get("volume", 0)),
                            "high_24h":         float(d.get("highPrice", 0)),
                            "low_24h":          float(d.get("lowPrice", 0)),
                            "timestamp":        datetime.now().isoformat(),
                            "source":           "binance",
                        }
                except Exception as e:
                    print(f"[binance] Erro 24h {ticker}: {e}", flush=True)

            if self._brapi_supported(ticker):
                try:
                    r = await client.get(
                        f"{_BRAPI_BASE}/quote/{ticker}",
                        params=self._brapi_params(),
                        headers=_BRAPI_HEADERS,
                    )
                    if r.status_code == 200:
                        items = r.json().get("results", [])
                        if items:
                            i = items[0]
                            price  = float(i.get("regularMarketPrice", 0) or 0)
                            change = float(i.get("regularMarketChangePercent", 0) or 0)
                            return {
                                "asset": ticker, "symbol": ticker,
                                "last_price":       price,
                                "price_change_pct": round(change, 2),
                                "volume":           int(i.get("regularMarketVolume", 0) or 0),
                                "high_24h":         float(i.get("regularMarketDayHigh", 0) or 0),
                                "low_24h":          float(i.get("regularMarketDayLow",  0) or 0),
                                "timestamp":        datetime.now().isoformat(),
                                "source":           "brapi",
                            }
                except Exception as e:
                    print(f"[brapi] Erro 24h {ticker}: {e}", flush=True)

            # Fallback Yahoo
            try:
                r = await client.get(
                    f"{_YF_BASE}/{self._yf_symbol(ticker)}",
                    params={"interval": "1d", "range": "2d"},
                    headers=_YF_HEADERS,
                )
                if r.status_code == 200:
                    meta       = r.json()["chart"]["result"][0]["meta"]
                    price      = float(meta.get("regularMarketPrice", 0))
                    prev_close = float(meta.get("chartPreviousClose", price) or price)
                    change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0
                    return {
                        "asset": ticker, "symbol": ticker,
                        "last_price":       price,
                        "price_change_pct": round(change_pct, 2),
                        "volume":           int(meta.get("regularMarketVolume", 0) or 0),
                        "high_24h":         float(meta.get("regularMarketDayHigh", 0) or 0),
                        "low_24h":          float(meta.get("regularMarketDayLow",  0) or 0),
                        "timestamp":        datetime.now().isoformat(),
                        "source":           "yahoo",
                    }
            except Exception as e:
                print(f"[yahoo] Erro 24h {ticker}: {e}", flush=True)
        return None


# Instância global
if HTTPX_AVAILABLE:
    market_data_service  = BrapiMarketData()
    MARKET_DATA_AVAILABLE = True
else:
    market_data_service  = None
    MARKET_DATA_AVAILABLE = False
