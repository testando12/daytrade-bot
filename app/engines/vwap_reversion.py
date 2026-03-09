"""
Engine de VWAP Reversion v1  — Reversão ao Preço Médio Ponderado por Volume

Estratégia de Mean Reversion baseada em VWAP com confirmação de RSI e Volume.
Ideal para mercados LATERAIS (ADX < 20) com volatilidade moderada.

Conceito:
  O preço tende a retornar ao VWAP (preço médio ponderado por volume).
  Quando se afasta demais, há alta probabilidade de reversão.

Indicadores:
  1. VWAP (Volume Weighted Average Price) — proxy usando média ponderada por volume
  2. Desvio do VWAP — z-score do preço vs VWAP (±1.5 desvios = zona de entrada)
  3. RSI — confirma oversold (<30) ou overbought (>70)
  4. Volume — volume acima da média confirma interesse institucional
  5. ATR — define stop e take profit dinâmicos

Regras:
  COMPRA (Long):   preço < VWAP - 1.5σ  AND  RSI < 30  AND  volume ↑
  VENDA (Short):   preço > VWAP + 1.5σ  AND  RSI > 70  AND  volume ↑
  Stop:            0.8 × ATR
  Take Profit:     retorno ao VWAP  ou  1.0 × ATR
  Filtro:          Melhor com ADX < 20 (mercado lateral)

Perfil esperado:
  Win rate: 55-65%  |  R/R: 1.2-1.8  |  Trades/dia: 5-15
"""

from typing import Dict, List
import math


def _mean(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _stddev(vals: List[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


def _rsi(prices: List[float], period: int = 14) -> float:
    """RSI (Relative Strength Index)."""
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
    """ATR simplificado (usa variação de fechamento)."""
    if len(prices) < 2:
        return 0.0
    trs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    recent = trs[-period:] if len(trs) >= period else trs
    return _mean(recent)


def _vwap(prices: List[float], volumes: List[float], period: int = 20) -> float:
    """
    VWAP proxy — média ponderada por volume dos últimos N candles.
    Em produção com dados intraday completos, usaria cumulative VWAP.
    """
    if len(prices) < period or len(volumes) < period:
        return _mean(prices[-period:]) if prices else 0.0

    p = prices[-period:]
    v = volumes[-period:]

    total_vol = sum(v)
    if total_vol == 0:
        return _mean(p)

    return sum(p[i] * v[i] for i in range(len(p))) / total_vol


def _vwap_bands(prices: List[float], volumes: List[float], period: int = 20, mult: float = 1.5):
    """
    Calcula VWAP e bandas de desvio (±mult × stddev ponderado por volume).
    Retorna (vwap_value, upper_band, lower_band, vwap_std).
    """
    vwap_val = _vwap(prices, volumes, period)

    p = prices[-period:] if len(prices) >= period else prices
    v = volumes[-period:] if len(volumes) >= period else volumes

    total_vol = sum(v)
    if total_vol == 0:
        std = _stddev(p)
    else:
        # Desvio padrão ponderado por volume
        weighted_sq_diff = sum(v[i] * (p[i] - vwap_val) ** 2 for i in range(len(p)))
        variance = weighted_sq_diff / total_vol
        std = math.sqrt(max(0, variance))

    upper = vwap_val + mult * std
    lower = vwap_val - mult * std

    return vwap_val, upper, lower, std


class VWAPReversionAnalyzer:
    """
    Engine de VWAP Reversion v1 — detecta reversões ao preço médio ponderado por volume.

    Melhor performance em mercados laterais (ADX < 20).
    Stop curto (0.8 ATR) mantém perdas pequenas.
    Alvo: retorno ao VWAP.
    """

    # Score mínimo para considerar entrada válida
    VR_THRESHOLD = 0.50

    # Período para cálculo do VWAP
    VWAP_PERIOD = 20

    # Multiplicador de desvio para bandas (1.5σ)
    DEVIATION_MULT = 1.5

    # RSI thresholds
    RSI_OVERSOLD = 32
    RSI_OVERBOUGHT = 68

    # Volume mínimo vs média para confirmar (1.2× = 20% acima da média)
    VOL_MULTIPLIER = 1.2

    # Stop e TP em múltiplos de ATR
    STOP_ATR_MULT = 0.8
    TP_ATR_MULT = 1.0

    @staticmethod
    def calculate_vwap_score(prices: List[float], volumes: List[float]) -> Dict:
        """
        Calcula o score de VWAP Reversion para um ativo.

        Returns:
            Dict com vr_score (0-1), entry_valid, direction (LONG/SHORT/NONE),
            vwap, deviation_pct, rsi, volume_ratio, atr_pct e sub-scores.
        """
        MIN_PERIODS = VWAPReversionAnalyzer.VWAP_PERIOD + 2
        empty = {
            "vr_score": 0.0, "entry_valid": False, "valid": False,
            "direction": "NONE", "vwap": 0.0, "vwap_upper": 0.0, "vwap_lower": 0.0,
            "deviation_pct": 0.0, "rsi": 50.0, "volume_ratio": 0.0,
            "atr_pct": 0.0, "vwap_std": 0.0,
            "deviation_score": 0.0, "rsi_score": 0.0, "vol_score": 0.0,
            "current_price": prices[-1] if prices else 0.0,
            "vwap_deviation": 0.0, "atr": 0.0,
        }

        if len(prices) < MIN_PERIODS or len(volumes) < MIN_PERIODS:
            return empty

        current_price = prices[-1]
        if current_price <= 0:
            return empty

        # ── 1. Calcular VWAP e bandas ────────────────────────────────────
        vwap_val, upper, lower, vwap_std = _vwap_bands(
            prices, volumes,
            VWAPReversionAnalyzer.VWAP_PERIOD,
            VWAPReversionAnalyzer.DEVIATION_MULT
        )

        if vwap_val <= 0 or vwap_std <= 0:
            return {**empty, "vwap": vwap_val, "current_price": current_price}

        # ── 2. Desvio do VWAP (z-score) ─────────────────────────────────
        deviation = (current_price - vwap_val) / vwap_std if vwap_std > 0 else 0.0
        deviation_pct = (current_price - vwap_val) / vwap_val

        # ── 3. RSI ──────────────────────────────────────────────────────
        rsi = _rsi(prices)

        # ── 4. Volume de confirmação ────────────────────────────────────
        vol_lookback = volumes[-VWAPReversionAnalyzer.VWAP_PERIOD:]
        avg_vol = _mean(vol_lookback[:-1]) if len(vol_lookback) > 1 else (_mean(vol_lookback) or 1.0)
        last_vol = volumes[-1] if volumes else 0.0
        volume_ratio = last_vol / avg_vol if avg_vol > 0 else 0.0

        # ── 5. ATR para stop/TP ─────────────────────────────────────────
        atr_val = _atr(prices)
        atr_pct = atr_val / current_price if current_price > 0 else 0.0

        # ── 6. Detectar direção e calcular sub-scores ───────────────────

        direction = "NONE"
        deviation_score = 0.0
        rsi_score = 0.0
        vol_score = 0.0

        # === LONG (oversold — preço abaixo do VWAP - 1.5σ) ===
        if current_price < lower:
            direction = "LONG"
            # Quão longe abaixo da banda inferior (mais longe = sinal mais forte)
            # 1.5σ = score 0.4, 2.0σ = 0.6, 2.5σ = 0.8, 3.0σ = 1.0
            deviation_score = min(1.0, max(0.0, (abs(deviation) - 1.0) / 2.0))

            # RSI confirma oversold
            if rsi < 25:
                rsi_score = 1.0
            elif rsi < VWAPReversionAnalyzer.RSI_OVERSOLD:
                rsi_score = 0.7
            elif rsi < 40:
                rsi_score = 0.3  # não ideal, mas price action fala mais
            else:
                rsi_score = 0.0  # RSI não confirma

        # === SHORT (overbought — preço acima do VWAP + 1.5σ) ===
        elif current_price > upper:
            direction = "SHORT"
            deviation_score = min(1.0, max(0.0, (abs(deviation) - 1.0) / 2.0))

            if rsi > 75:
                rsi_score = 1.0
            elif rsi > VWAPReversionAnalyzer.RSI_OVERBOUGHT:
                rsi_score = 0.7
            elif rsi > 60:
                rsi_score = 0.3
            else:
                rsi_score = 0.0

        if direction == "NONE":
            return {**empty, "valid": True, "vwap": round(vwap_val, 6),
                    "vwap_upper": round(upper, 6), "vwap_lower": round(lower, 6),
                    "deviation_pct": round(deviation_pct, 6), "rsi": round(rsi, 2),
                    "volume_ratio": round(volume_ratio, 3), "atr_pct": round(atr_pct, 6),
                    "vwap_std": round(vwap_std, 6), "current_price": round(current_price, 6)}

        # Volume confirma
        if volume_ratio >= VWAPReversionAnalyzer.VOL_MULTIPLIER * 1.5:
            vol_score = 1.0
        elif volume_ratio >= VWAPReversionAnalyzer.VOL_MULTIPLIER:
            vol_score = 0.6
        elif volume_ratio >= 0.8:
            vol_score = 0.3  # volume OK mas não forte
        else:
            vol_score = 0.0

        # ── 7. Score final ponderado ────────────────────────────────────
        # Deviation do VWAP é o sinal principal (40%)
        # RSI confirma exaustão (35%)
        # Volume valida interesse institucional (25%)
        raw_score = (
            0.40 * deviation_score
            + 0.35 * rsi_score
            + 0.25 * vol_score
        )

        # Boost: se todos os 3 sinais concordam (>0.3), dá confiança extra
        if deviation_score > 0.3 and rsi_score > 0.3 and vol_score > 0.3:
            raw_score = min(1.0, raw_score * 1.15)

        # Penalidade: volume fraco reduz confiança
        if volume_ratio < 0.8:
            raw_score *= 0.5

        vr_score = round(raw_score, 4)
        entry_valid = vr_score >= VWAPReversionAnalyzer.VR_THRESHOLD

        return {
            "vr_score": vr_score,
            "entry_valid": entry_valid,
            "valid": True,
            "direction": direction,
            "vwap": round(vwap_val, 6),
            "vwap_upper": round(upper, 6),
            "vwap_lower": round(lower, 6),
            "deviation_pct": round(deviation_pct, 6),
            "deviation_z": round(deviation, 3),
            "rsi": round(rsi, 2),
            "volume_ratio": round(volume_ratio, 3),
            "atr_pct": round(atr_pct, 6),
            "vwap_std": round(vwap_std, 6),
            "deviation_score": round(deviation_score, 4),
            "rsi_score": round(rsi_score, 4),
            "vol_score": round(vol_score, 4),
            "stop_atr_mult": VWAPReversionAnalyzer.STOP_ATR_MULT,
            "tp_atr_mult": VWAPReversionAnalyzer.TP_ATR_MULT,
            "current_price": round(current_price, 6),
            # Aliases para compatibilidade com main.py
            "vwap_deviation": round(deviation_pct, 6),
            "atr": round(atr_val, 6),
        }

    @staticmethod
    def calculate_multiple_assets(
        assets_data: Dict[str, Dict],
        top_n: int = 2,
    ) -> Dict[str, Dict]:
        """
        Analisa todos os ativos e retorna os top_n candidatos para VWAP Reversion.

        Args:
            assets_data: {asset: {prices: [...], volumes: [...]}}
            top_n: máx de candidatos retornados

        Returns:
            Dict {asset: resultado} dos candidatos válidos, ordenados por vr_score desc.
        """
        results = {}
        for asset, data in assets_data.items():
            prices = data.get("prices", [])
            volumes = data.get("volumes", [])
            result = VWAPReversionAnalyzer.calculate_vwap_score(prices, volumes)
            if result["entry_valid"]:
                results[asset] = result

        sorted_results = sorted(
            results.items(),
            key=lambda x: x[1]["vr_score"],
            reverse=True
        )
        return dict(sorted_results[:top_n])
