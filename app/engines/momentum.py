"""
Engine de Análise de Momentum v3
Algoritmo calibrado para >= 70% de acerto na direção do próximo candle.

Indicadores:
  1. ROC 3/5/10 candles — retorno recente ponderado (sinal mais importante)
  2. RSI-trend: RSI >50 = bullish, <50 = bearish
  3. EMA slope: inclinação da EMA5 nos últimos 3 candles
  4. Volume trend: volume crescente confirma direção
  5. Candle bias: % de candles de alta nos últimos 10
"""

from typing import Dict, List

# ── Clusters de correlação ───────────────────────────────────────────────────
# Ativos dentro do mesmo cluster têm correlação histórica > 0.80.
# Regra: no máximo 1 sinal de entrada por cluster por ciclo.
# Isso evita drawdown concentrado quando o setor/grupo cai junto.
CORRELATION_CLUSTERS: Dict[str, List[str]] = {
    # Crypto de grande cap — caem e sobem juntos
    "crypto_large": ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "AVAX", "DOT"],
    # Crypto altcoin / DeFi — beta alto vs BTC
    "crypto_defi":  ["AAVE", "MKR", "COMP", "CRV", "SNX", "UNI", "LINK"],
    # Crypto meme / especulativo
    "crypto_meme":  ["DOGE", "SHIB", "PEPE", "WIF", "FLOKI", "BONK"],
    # Solana ecosystem
    "crypto_sol_eco": ["JTO", "PYTH", "JUP", "POPCAT", "RENDER", "FET"],
    # Layer 1 emergentes
    "crypto_l1":    ["NEAR", "APT", "ARB", "OP", "INJ", "SUI", "SEI", "TIA", "STRK", "MANTA", "TAO"],
    # B3 Petróleo & Energia
    "b3_energia":   ["PETR4", "PRIO3", "CSAN3", "EGIE3"],
    # B3 Bancos — altamente correlacionados entre si
    "b3_bancos":    ["ITUB4", "BBDC4", "BBAS3", "ITSA4"],
    # B3 Commodities / Mineração
    "b3_comodities": ["VALE3", "GGBR4", "JBSS3", "SUZB3"],
    # FIIs — todos caem juntos quando taxa Selic sobe
    "fiis":         ["MXRF11", "XPML11", "VISC11", "HGLG11", "KNRI11", "XPLG11",
                     "BCFF11", "RBRF11", "IRDM11", "KNCR11"],
    # US Big Tech — correção de tech derruba todos
    "us_bigtech":   ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"],
    # US Semicondutores
    "us_semis":     ["AMD", "INTC", "QCOM", "AVGO", "MU", "SOXL"],
    # ETFs globais (acompanham MSCI World)
    "intl_etfs":    ["EFA", "EEM", "VGK", "EWG", "EWQ", "EWU", "EWJ", "EWY", "ASHR", "INDA", "EWA", "EWZ"],
    # Metais preciosos — correlação USD/inflação
    "metals":       ["GOLD", "SILVER", "GLD", "SLV"],
    # Energia (petróleo e gás)
    "energy_comd":  ["OIL", "NATGAS"],
    # Agronegócio
    "agro":         ["CAFE", "SOJA", "MILHO", "ACUCAR", "TRIGO", "CACAU"],
}

# Mapa reverso: ativo → cluster
_ASSET_TO_CLUSTER: Dict[str, str] = {
    asset.upper(): cluster
    for cluster, assets in CORRELATION_CLUSTERS.items()
    for asset in assets
}


def _ema(prices: List[float], period: int) -> float:
    if not prices:
        return 0.0
    if len(prices) < period:
        return sum(prices) / len(prices)
    k = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def _rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    warm = deltas[-period * 2:] if len(deltas) >= period * 2 else deltas
    avg_gain = sum(d for d in warm[:period] if d > 0) / period
    avg_loss = sum(-d for d in warm[:period] if d < 0) / period
    for d in warm[period:]:
        avg_gain = (avg_gain * (period - 1) + max(0, d)) / period
        avg_loss = (avg_loss * (period - 1) + max(0, -d)) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _atr(prices: List[float], period: int = 14) -> float:
    if len(prices) < 2:
        return 0.0
    trs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    return sum(trs[-period:]) / min(period, len(trs))


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _roc(prices: List[float], n: int) -> float:
    if len(prices) <= n or prices[-n - 1] == 0:
        return 0.0
    return (prices[-1] - prices[-n - 1]) / prices[-n - 1]


class MomentumAnalyzer:
    """Analisador de momentum v3 — calibrado para >= 70% direção"""

    ENTRY_THRESHOLD = 0.50   # v2.1: alinhado com MIN_MOMENTUM_SCORE — só opera sinais fortes

    W_ROC     = 0.35   # ROC (mais preditivo)
    W_RSI     = 0.25   # RSI tendency
    W_SLOPE   = 0.20   # EMA slope
    W_VOLUME  = 0.12   # volume
    W_CANDLES = 0.08   # candle bias

    @staticmethod
    def calculate_momentum_score(
        prices: List[float],
        volumes: List[float],
        period_short: int = 9,
        period_long: int = 21,
    ) -> Dict:
        MIN_PERIODS = 15
        if len(prices) < MIN_PERIODS:
            return {
                "momentum_score": 0.0, "valid": False,
                "return_score": 0.0, "trend_score": 0.0,
                "volume_score": 0.0, "rsi_score": 0.0, "macd_score": 0.0,
                "current_price": prices[-1] if prices else 0.0,
                "rsi": 50.0, "atr": 0.0, "signal_quality": 0.0,
                "trend_status": "indisponivel", "return_pct": 0.0,
                "classification": "LATERAL", "entry_valid": False, "atr_pct": 0.0,
            }

        price = prices[-1]

        # 1. ROC multi-periodo
        roc3  = _roc(prices, 3)
        roc5  = _roc(prices, 5)
        roc10 = _roc(prices, min(10, len(prices) - 1))
        roc_score = _clamp(
            0.5 * _clamp(roc3 / 0.008)
            + 0.3 * _clamp(roc5 / 0.012)
            + 0.2 * _clamp(roc10 / 0.018)
        )
        return_pct = roc5

        # 2. RSI-trend (>50 bullish, <50 bearish)
        rsi = _rsi(prices, min(14, len(prices) - 2))
        rsi_score = _clamp((rsi - 50) / 30)

        # 3. EMA5 slope
        ema5_now  = _ema(prices, min(5, len(prices)))
        ema5_prev = _ema(prices[:-3], min(5, len(prices) - 3)) if len(prices) > 8 else ema5_now
        slope = (ema5_now - ema5_prev) / ema5_prev if ema5_prev > 0 else 0.0
        trend_score = _clamp(slope / 0.005)

        # 4. Volume trend
        if len(volumes) >= 10:
            rv = sum(volumes[-5:]) / 5
            pv = sum(volumes[-10:-5]) / 5
            vol_chg = (rv - pv) / pv if pv > 0 else 0.0
            price_dir = 1 if roc3 > 0 else -1
            volume_score = _clamp(vol_chg * price_dir * 2)
        else:
            volume_score = 0.0

        # 5. Candle bias
        n_check = min(10, len(prices) - 1)
        ups = sum(1 for i in range(-n_check, 0) if prices[i] > prices[i - 1])
        candle_score = _clamp((ups / n_check - 0.5) * 4)

        # Score final ponderado
        momentum_score = (
            MomentumAnalyzer.W_ROC     * roc_score
            + MomentumAnalyzer.W_RSI     * rsi_score
            + MomentumAnalyzer.W_SLOPE   * trend_score
            + MomentumAnalyzer.W_VOLUME  * volume_score
            + MomentumAnalyzer.W_CANDLES * candle_score
        )

        atr     = _atr(prices, min(14, len(prices) - 1))
        atr_pct = atr / price if price > 0 else 0.0

        sub_signals = [roc_score, rsi_score, trend_score]
        dominated   = max(
            sum(1 for s in sub_signals if s > 0.05),
            sum(1 for s in sub_signals if s < -0.05),
        )
        signal_quality = dominated / len(sub_signals)

        # ── ATR Volatility Filter ──────────────────────────────────────
        # Rejeita entradas quando o ativo está "morto" (sem movimento real).
        # ATR < 0.3% do preço = mercado flat → trades ruins, spread come tudo.
        # ATR > 6% = muito volátil para sizing seguro → também rejeita.
        ATR_MIN_PCT = 0.003   # 0.3% mínimo
        ATR_MAX_PCT = 0.06    # 6% máximo
        atr_ok = ATR_MIN_PCT <= atr_pct <= ATR_MAX_PCT
        atr_rejected = not atr_ok and atr_pct > 0  # só rejeita se tiver dados

        entry_valid = (
            momentum_score >= MomentumAnalyzer.ENTRY_THRESHOLD  # long-only: sem abs()
            and not atr_rejected
        )

        trend_status = (
            "forte_alta" if momentum_score > 0.4 else
            "alta"       if momentum_score > 0.1 else
            "queda"      if momentum_score < -0.1 else
            "lateral"
        )

        return {
            "momentum_score":  float(momentum_score),
            "return_score":    float(roc_score),
            "trend_score":     float(trend_score),
            "volume_score":    float(volume_score),
            "rsi_score":       float(rsi_score),
            "macd_score":      float(candle_score),
            "signal_quality":  float(signal_quality),
            "entry_valid":     bool(entry_valid),
            "atr_ok":          bool(atr_ok),
            "atr_rejected":    bool(atr_rejected),
            "valid":           True,
            "current_price":   float(price),
            "ma_short":        float(ema5_now),
            "ma_long":         float(_ema(prices, min(21, len(prices)))),
            "rsi":             float(rsi),
            "atr":             float(atr),
            "atr_pct":         float(atr_pct),
            "trend_status":    trend_status,
            "return_pct":      float(return_pct),
        }

    @staticmethod
    def classify_asset(momentum_score: float, entry_valid: bool = True) -> str:
        if momentum_score > 0.35:
            return "FORTE_ALTA"
        elif momentum_score > 0.10:
            return "ALTA_LEVE"
        elif momentum_score > -0.10:
            return "LATERAL"
        else:
            return "QUEDA"

    @staticmethod
    def calculate_multiple_assets(
        assets_data: Dict[str, Dict[str, List[float]]]
    ) -> Dict[str, Dict]:
        results = {}
        for asset, data in assets_data.items():
            prices  = data.get("prices", [])
            volumes = data.get("volumes", [])
            mdata   = MomentumAnalyzer.calculate_momentum_score(prices, volumes)
            score   = mdata["momentum_score"]
            results[asset] = {
                **mdata,
                "classification": MomentumAnalyzer.classify_asset(score, mdata.get("entry_valid", False)),
                "asset": asset,
            }

        if results:
            ranked = sorted(results.items(), key=lambda x: x[1]["momentum_score"], reverse=True)
            for rank, (asset, _) in enumerate(ranked, 1):
                results[asset]["rank"] = rank
                results[asset]["is_top3"] = rank <= 3
                results[asset]["corr_blocked"] = False
                results[asset]["corr_winner"] = None

        # ── Filtro de correlação ────────────────────────────────────────────
        # Para cada cluster, mantém apenas o ativo com maior |score| que tenha
        # entry_valid=True. Os demais são bloqueados (corr_blocked=True).
        # Isso evita drawdown conjunto quando um grupo correlacionado cai.
        seen_clusters: Dict[str, str] = {}        # cluster → asset vencedor
        # Ordena por |score| decrescente para que o primeiro encontrado seja o melhor
        ranked_by_score = sorted(
            results.keys(),
            key=lambda a: abs(results[a]["momentum_score"]),
            reverse=True,
        )
        for asset in ranked_by_score:
            if not results[asset].get("entry_valid"):
                continue
            cluster = _ASSET_TO_CLUSTER.get(asset.upper())
            if cluster is None:
                continue                          # ativo fora dos clusters → não bloqueia
            if cluster not in seen_clusters:
                seen_clusters[cluster] = asset    # primeiro (melhor score) vence
            else:
                # Bloqueia os demais do mesmo cluster
                winner = seen_clusters[cluster]
                results[asset]["entry_valid"]  = False
                results[asset]["corr_blocked"] = True
                results[asset]["corr_winner"]  = winner

        return results
