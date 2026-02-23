"""
Engine de Análise de Momentum v2
Indicadores usados:
- Retorno multi-período (5, 10, 20 candles)
- EMA rápida vs EMA lenta (sinal MACD-like)
- Confirmação de volume (preço sobe + volume sobe = forte)
- RSI integrado como filtro de entrada
- ATR para medir volatilidade e qualidade do sinal
- Score final ponderado com filtro de ruído
"""

import math
from typing import Dict, List
from app.core.config import settings


def _ema(prices: List[float], period: int) -> float:
    """Exponential Moving Average — mais reativo que a média simples."""
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0.0
    k = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def _rsi(prices: List[float], period: int = 14) -> float:
    """RSI clássico de Wilder."""
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d for d in deltas[:period] if d > 0]
    losses = [-d for d in deltas[:period] if d < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for d in deltas[period:]:
        avg_gain = (avg_gain * (period - 1) + max(0, d)) / period
        avg_loss = (avg_loss * (period - 1) + max(0, -d)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(prices: List[float], period: int = 14) -> float:
    """Average True Range — mede volatilidade real."""
    if len(prices) < 2:
        return 0.0
    trs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    recent = trs[-period:] if len(trs) >= period else trs
    return sum(recent) / len(recent) if recent else 0.0


def _normalize(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


class MomentumAnalyzer:
    """Analisador de momentum aprimorado v2"""

    # Pesos dos componentes (somam 1.0)
    W_RETURN   = 0.25   # retorno multi-período
    W_EMA      = 0.25   # cruzamento EMA rápida/lenta
    W_VOLUME   = 0.20   # confirmação de volume
    W_RSI      = 0.15   # RSI como filtro de entrada
    W_MACD     = 0.15   # diferença MACD normalizada

    # Filtro de qualidade: só entra em posição se score >= ENTRY_THRESHOLD
    # 0.10 = sinal líquido modesto; 0.30 era muito estrito para dados reais
    ENTRY_THRESHOLD = 0.10

    @staticmethod
    def calculate_momentum_score(
        prices: List[float],
        volumes: List[float],
        period_short: int = 9,
        period_long: int = 21,
    ) -> Dict:
        """
        Calcula Momentum Score composto v2.

        Componentes:
          1. Retorno multi-período — média ponderada de retornos em 5, 10, 20 candles
          2. Cruzamento EMA 9/21 — sinal de tendência suavizado
          3. Confirmação de volume — alta com volume crescente = sinal forte
          4. RSI bias — RSI 30-70 = neutro, <30 = sobrevendido (compra), >70 = sobrecomprado (venda)
          5. MACD-like — EMA12 - EMA26 normalizado pelo preço
        """
        MIN_PERIODS = 22
        if len(prices) < MIN_PERIODS or len(volumes) < MIN_PERIODS:
            return {
                "momentum_score": 0.0, "valid": False,
                "return_score": 0.0, "trend_score": 0.0,
                "volume_score": 0.0, "rsi_score": 0.0, "macd_score": 0.0,
                "current_price": prices[-1] if prices else 0.0,
                "rsi": 50.0, "atr": 0.0, "signal_quality": 0.0,
                "trend_status": "indisponível", "return_pct": 0.0,
                "classification": "LATERAL", "entry_valid": False,
            }

        price = prices[-1]

        # ── 1. Retorno multi-período ──────────────────────────────
        def ret(n):
            if prices[-n - 1] == 0:
                return 0.0
            return (price - prices[-n - 1]) / prices[-n - 1]

        r5  = _normalize(ret(5)  / 0.05)   # normalizado: 5% = score 1.0
        r10 = _normalize(ret(10) / 0.08)
        r20 = _normalize(ret(20) / 0.12)
        return_score = (0.5 * r5 + 0.3 * r10 + 0.2 * r20)
        return_pct = ret(5)

        # ── 2. Cruzamento EMA curta/longa ─────────────────────────
        ema_fast = _ema(prices, period_short)
        ema_slow = _ema(prices, period_long)
        if ema_slow == 0:
            ema_cross = 0.0
        else:
            ema_cross = (ema_fast - ema_slow) / ema_slow
        trend_score = _normalize(ema_cross / 0.02)  # 2% diferença = score máximo

        # ── 3. Confirmação de volume ──────────────────────────────
        avg_vol = sum(volumes[-20:]) / 20
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
        # Volume alto com preço subindo = bullish; volume alto com preço caindo = bearish
        price_direction = 1 if prices[-1] >= prices[-2] else -1
        volume_score = _normalize((vol_ratio - 1.0) * price_direction)

        # ── 4. RSI — filtro de entrada ────────────────────────────
        rsi = _rsi(prices, 14)
        if rsi < 30:
            # Sobrevendido: oportunidade de compra
            rsi_score = _normalize((30 - rsi) / 30, 0.0, 1.0)
        elif rsi > 70:
            # Sobrecomprado: sinal de venda
            rsi_score = _normalize((rsi - 70) / 30, -1.0, 0.0) * -1
        else:
            # Zona neutra: pontuação proporcional ao centro
            rsi_score = _normalize((rsi - 50) / 20)

        # ── 5. MACD-like (EMA12 - EMA26) ─────────────────────────
        ema12 = _ema(prices, 12)
        ema26 = _ema(prices, 26) if len(prices) >= 26 else ema_slow
        if ema26 == 0:
            macd_val = 0.0
        else:
            macd_val = (ema12 - ema26) / ema26
        macd_score = _normalize(macd_val / 0.015)

        # ── Score final ponderado ─────────────────────────────────
        momentum_score = (
            MomentumAnalyzer.W_RETURN * return_score
            + MomentumAnalyzer.W_EMA    * trend_score
            + MomentumAnalyzer.W_VOLUME * volume_score
            + MomentumAnalyzer.W_RSI    * rsi_score
            + MomentumAnalyzer.W_MACD   * macd_score
        )

        # ── ATR e qualidade do sinal ──────────────────────────────
        atr = _atr(prices, 14)
        atr_pct = atr / price if price > 0 else 0.0
        # Sinal de qualidade: scores concordam entre si?
        scores = [return_score, trend_score, macd_score]
        positives = sum(1 for s in scores if s > 0.1)
        negatives = sum(1 for s in scores if s < -0.1)
        agreement = abs(positives - negatives) / len(scores)
        signal_quality = agreement  # 0 = discordância total, 1 = todos concordam

        # Entrada válida: score mínimo + concordância parcial + ATR razoável
        # signal_quality >= 0.33 = 2 de 3 indicadores de tendência concordam
        entry_valid = (
            abs(momentum_score) >= MomentumAnalyzer.ENTRY_THRESHOLD
            and signal_quality >= 0.33
            and atr_pct > 0.001  # ativo com volatilidade mínima
        )

        trend_status = (
            "forte_alta" if trend_score > 0.4 else
            "alta"       if trend_score > 0.1 else
            "queda"      if trend_score < -0.1 else
            "lateral"
        )

        return {
            "momentum_score":  float(momentum_score),
            "return_score":    float(return_score),
            "trend_score":     float(trend_score),
            "volume_score":    float(volume_score),
            "rsi_score":       float(rsi_score),
            "macd_score":      float(macd_score),
            "signal_quality":  float(signal_quality),
            "entry_valid":     entry_valid,
            "valid":           True,
            "current_price":   float(price),
            "ma_short":        float(ema_fast),
            "ma_long":         float(ema_slow),
            "rsi":             float(rsi),
            "atr":             float(atr),
            "atr_pct":         float(atr_pct),
            "trend_status":    trend_status,
            "return_pct":      float(return_pct),
        }

    @staticmethod
    def classify_asset(momentum_score: float, entry_valid: bool = True) -> str:
        """
        Classifica um ativo pelo Momentum Score.
        Threshold mais alto filtra sinais fracos.
        """
        if momentum_score > 0.45:
            return "FORTE_ALTA"
        elif momentum_score > 0.25:
            return "ALTA_LEVE"
        elif momentum_score > -0.25:
            return "LATERAL"
        else:
            return "QUEDA"

    @staticmethod
    def calculate_multiple_assets(
        assets_data: Dict[str, Dict[str, List[float]]]
    ) -> Dict[str, Dict]:
        """
        Calcula momentum score para múltiplos ativos.
        Inclui ranking relativo entre os ativos para priorizar os melhores.
        """
        results = {}

        for asset, data in assets_data.items():
            prices  = data.get("prices", [])
            volumes = data.get("volumes", [])
            momentum_data = MomentumAnalyzer.calculate_momentum_score(prices, volumes)
            score = momentum_data["momentum_score"]
            entry_valid = momentum_data.get("entry_valid", False)
            classification = MomentumAnalyzer.classify_asset(score, entry_valid)
            results[asset] = {
                **momentum_data,
                "classification": classification,
                "asset": asset,
            }

        # Ranking relativo: qual ativo tem o maior momentum?
        if results:
            scores = {a: d["momentum_score"] for a, d in results.items()}
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            for rank, (asset, _) in enumerate(ranked, 1):
                results[asset]["rank"] = rank
                results[asset]["is_top3"] = rank <= 3

        return results
