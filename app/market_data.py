"""
Servico de dados de mercado - Yahoo Finance (B3 / Bovespa)
Fornece precos em tempo real e historico de acoes brasileiras.

Usa a API publica do Yahoo Finance (v8/finance/chart)  gratuita, sem token.
Acoes B3 usam o sufixo .SA (ex.: PETR4.SA, VALE3.SA).
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from app.core.config import settings


_YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Yahoo Finance interval -> range mapping
_YF_RANGE = {
    "1m": "1d", "2m": "5d", "5m": "5d",
    "15m": "5d", "30m": "1mo", "60m": "1mo",
    "1h": "1mo", "1d": "6mo",
}


class BrapiMarketData:
    """
    Cliente de dados de mercado da B3 via Yahoo Finance.
    Compativel com a interface anterior (BrapiMarketData).
    Suporta PETR4, VALE3, ITUB4, BBDC4, ABEV3, WEGE3, MGLU3, BBAS3, ITSA4, RENT3.
    """

    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
    VALID_INTERVALS = ["1m", "2m", "5m", "15m", "30m", "60m", "1h", "1d"]

    def __init__(self):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx nao instalado. Execute: pip install httpx")
        self.timeout = getattr(settings, "MARKET_API_TIMEOUT", 15)

    # Criptomoedas que usam sufixo -USD no Yahoo Finance
    CRYPTO_SYMBOLS: set = {"BTC","ETH","BNB","SOL","ADA","XRP","DOGE","DOT","AVAX","MATIC","LINK","LTC","UNI","ATOM","TRX"}

    def _yf_symbol(self, asset: str) -> str:
        """
        Converte símbolo para Yahoo Finance:
          B3:    PETR4  -> PETR4.SA
          Crypto: BTC   -> BTC-USD
        """
        s = asset.upper()
        if s.endswith(".SA") or s.endswith("-USD"):
            return s
        if s in self.CRYPTO_SYMBOLS:
            return f"{s}-USD"
        return f"{s}.SA"

    async def _fetch_single_price(self, client, asset: str) -> tuple:
        """Retorna (asset, price) ou (asset, None) em caso de falha."""
        symbol = self._yf_symbol(asset)
        try:
            r = await client.get(
                f"{self.BASE_URL}/{symbol}",
                params={"interval": "1m", "range": "1d"},
                headers=_YF_HEADERS,
            )
            if r.status_code == 200:
                meta = r.json()["chart"]["result"][0]["meta"]
                price = meta.get("regularMarketPrice")
                if price is not None:
                    return (asset.upper(), float(price))
        except Exception as e:
            print(f"[yahoo] Erro ao obter preco de {asset}: {e}", flush=True)
        return (asset.upper(), None)

    async def get_current_price(self, asset: str) -> Optional[Dict]:
        """Preco atual de um ativo da B3."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            sym, price = await self._fetch_single_price(client, asset)
            if price is not None:
                return {
                    "asset":     sym,
                    "symbol":    sym,
                    "price":     price,
                    "timestamp": datetime.now().isoformat(),
                }
        return None

    async def get_all_prices(self, assets: Optional[List[str]] = None) -> Dict[str, float]:
        """
        Precos atuais de multiplos ativos de forma concorrente.
        Retorna: {"PETR4": 38.69, "VALE3": 88.06, ...}
        """
        if assets is None:
            assets = settings.ALLOWED_ASSETS

        prices: Dict[str, float] = {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                results = await asyncio.gather(
                    *[self._fetch_single_price(client, a) for a in assets],
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, tuple):
                        sym, price = result
                        if price is not None:
                            prices[sym] = price
        except Exception as e:
            print(f"[yahoo] Erro ao obter precos: {e}", flush=True)
        return prices

    async def get_klines(
        self,
        asset: str,
        interval: str = "5m",
        limit: int = 25,
    ) -> Optional[Dict]:
        """Candles historicos de um ativo da B3."""
        ticker = asset.upper()
        if interval not in self.VALID_INTERVALS:
            interval = "5m"
        yf_range = _YF_RANGE.get(interval, "5d")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    f"{self.BASE_URL}/{self._yf_symbol(ticker)}",
                    params={"interval": interval, "range": yf_range},
                    headers=_YF_HEADERS,
                )
                if r.status_code != 200:
                    print(f"[yahoo] Erro {r.status_code} para klines {ticker}", flush=True)
                    return None

                chart_result = r.json()["chart"]["result"]
                if not chart_result:
                    return None

                res = chart_result[0]
                timestamps_raw = res.get("timestamp", [])
                quotes = res.get("indicators", {}).get("quote", [{}])[0]

                closes  = quotes.get("close",  [])
                highs   = quotes.get("high",   [])
                lows    = quotes.get("low",    [])
                volumes = quotes.get("volume", [])

                valid = [i for i in range(len(closes)) if closes[i] is not None]
                valid = valid[-limit:]

                prices_list  = [float(closes[i]) for i in valid]
                volumes_list = [float(volumes[i]) if volumes and volumes[i] is not None else 0.0 for i in valid]
                highs_list   = [float(highs[i])   if highs   and highs[i]   is not None else 0.0 for i in valid]
                lows_list    = [float(lows[i])    if lows    and lows[i]    is not None else 0.0 for i in valid]
                ts_list      = [timestamps_raw[i] if i < len(timestamps_raw) else 0 for i in valid]

                if not prices_list:
                    return None

                return {
                    "asset":      ticker,
                    "symbol":     ticker,
                    "interval":   interval,
                    "prices":     prices_list,
                    "volumes":    volumes_list,
                    "highs":      highs_list,
                    "lows":       lows_list,
                    "timestamps": ts_list,
                    "count":      len(prices_list),
                }
        except Exception as e:
            print(f"[yahoo] Erro ao obter klines de {ticker}: {e}", flush=True)
        return None

    async def get_all_klines(
        self,
        assets: Optional[List[str]] = None,
        interval: str = "5m",
        limit: int = 25,
    ) -> Dict[str, Dict]:
        """Klines de multiplos ativos de forma concorrente."""
        if assets is None:
            assets = settings.ALLOWED_ASSETS

        tasks = [self.get_klines(a, interval, limit) for a in assets]
        results = await asyncio.gather(*tasks)

        market_data: Dict[str, Dict] = {}
        for asset, klines in zip(assets, results):
            if klines and klines["count"] > 0:
                market_data[asset.upper()] = {
                    "prices":  klines["prices"],
                    "volumes": klines["volumes"],
                }
        return market_data

    async def get_24h_ticker(self, asset: str) -> Optional[Dict]:
        """Estatisticas de 24h de um ativo da B3."""
        ticker = asset.upper()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    f"{self.BASE_URL}/{self._yf_symbol(ticker)}",
                    params={"interval": "1d", "range": "2d"},
                    headers=_YF_HEADERS,
                )
                if r.status_code == 200:
                    meta = r.json()["chart"]["result"][0]["meta"]
                    price      = float(meta.get("regularMarketPrice", 0))
                    prev_close = float(meta.get("chartPreviousClose", price) or price)
                    change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0
                    volume     = int(meta.get("regularMarketVolume", 0) or 0)
                    day_high   = float(meta.get("regularMarketDayHigh", 0) or 0)
                    day_low    = float(meta.get("regularMarketDayLow",  0) or 0)

                    return {
                        "asset":            ticker,
                        "symbol":           ticker,
                        "last_price":       price,
                        "price_change_pct": round(change_pct, 2),
                        "volume":           volume,
                        "high_24h":         day_high,
                        "low_24h":          day_low,
                        "timestamp":        datetime.now().isoformat(),
                    }
        except Exception as e:
            print(f"[yahoo] Erro ao obter 24h ticker de {ticker}: {e}", flush=True)
        return None


# Instancia global
if HTTPX_AVAILABLE:
    market_data_service = BrapiMarketData()
    MARKET_DATA_AVAILABLE = True
else:
    market_data_service = None
    MARKET_DATA_AVAILABLE = False
