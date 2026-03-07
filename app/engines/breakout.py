"""
Engine de Breakout v1

Detecta rompimentos de resistência/suporte com confirmação de volume.
Operação de alta probabilidade: só entra quando o preço rompe com força real.

Indicadores:
  1. Resistência/Suporte (máxima/mínima dos últimos N candles)
  2. Volume de confirmação — volume >= 1.5× a média (sem volume, sem breakout)
  3. ATR filter — rompimento mínimo de 0.5% para evitar falsos sinais
  4. Momentum pós-breakout — ROC curto confirma a direção
  5. Distância relativa — quanto mais longe da resistência, mais forte o sinal

Só considera entrada válida quando:
  - Preço rompe resistência (LONG) ou suporte (SHORT) por pelo menos MIN_BREAKOUT_PCT
  - Volume do candle de breakout >= VOL_MULTIPLIER × média
  - Score total >= BO_THRESHOLD (0.60)
"""

from typing import Dict, List


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stddev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return (sum((v - m) ** 2 for v in values) / len(values)) ** 0.5


def _atr(prices: List[float], period: int = 14) -> float:
    """Average True Range simplificado (sem high/low — usa variação de fechamento)."""
    if len(prices) < 2:
        return 0.0
    trs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    recent = trs[-period:] if len(trs) >= period else trs
    return _mean(recent) if recent else 0.0


def _roc(prices: List[float], period: int = 5) -> float:
    """Rate of Change percentual."""
    if len(prices) < period + 1:
        return 0.0
    base = prices[-(period + 1)]
    if base == 0:
        return 0.0
    return (prices[-1] - base) / base


class BreakoutAnalyzer:
    """Engine de Breakout v1 — detecta rompimentos de resistência/suporte com volume."""

    # Score mínimo para considerar entrada válida
    BO_THRESHOLD = 0.60

    # Lookback para calcular resistência/suporte
    LOOKBACK = 20

    # Volume mínimo vs média para confirmar o breakout (1.5× = 50% acima da média)
    VOL_MULTIPLIER = 1.5

    # Rompimento mínimo em % (0.5%) — evita falsos breakouts de ruído
    MIN_BREAKOUT_PCT = 0.005

    # ROC mínimo para confirmar momentum pós-breakout
    MIN_ROC_CONFIRM = 0.002

    @staticmethod
    def calculate_breakout_score(prices: List[float], volumes: List[float]) -> Dict:
        """
        Calcula o score de breakout para um ativo.

        Returns:
            Dict com breakout_score (0-1), entry_valid, direction (LONG/SHORT/NONE),
            resistance, support, atr_pct, volume_ratio, breakout_pct e sub-scores.
        """
        MIN_PERIODS = BreakoutAnalyzer.LOOKBACK + 2
        empty = {
            "breakout_score": 0.0, "entry_valid": False, "valid": False,
            "direction": "NONE", "resistance": 0.0, "support": 0.0,
            "atr_pct": 0.0, "volume_ratio": 0.0, "breakout_pct": 0.0,
            "breakout_score_raw": 0.0, "vol_score": 0.0,
            "momentum_score_raw": 0.0, "distance_score": 0.0,
            "current_price": prices[-1] if prices else 0.0,
        }
        if len(prices) < MIN_PERIODS:
            return empty

        current_price = prices[-1]
        if current_price <= 0:
            return empty

        # ── 1. Resistência e Suporte (excluí o candle atual) ─────────────────
        lookback_prices = prices[-(BreakoutAnalyzer.LOOKBACK + 1):-1]
        resistance = max(lookback_prices)
        support    = min(lookback_prices)

        if resistance <= 0 or support <= 0 or resistance == support:
            return empty

        # ── 2. ATR para filtrar rompimentos minúsculos ───────────────────────
        atr_val = _atr(prices[-(BreakoutAnalyzer.LOOKBACK + 1):])
        atr_pct = atr_val / current_price if current_price > 0 else 0.0

        # ── 3. Volume de confirmação ──────────────────────────────────────────
        vol_lookback = volumes[-(BreakoutAnalyzer.LOOKBACK):] if len(volumes) >= BreakoutAnalyzer.LOOKBACK else volumes
        avg_vol  = _mean(vol_lookback[:-1]) if len(vol_lookback) > 1 else (_mean(vol_lookback) or 1.0)
        last_vol = volumes[-1] if volumes else 0.0
        volume_ratio = last_vol / avg_vol if avg_vol > 0 else 0.0

        vol_score = 0.0
        if volume_ratio >= BreakoutAnalyzer.VOL_MULTIPLIER:
            # Score de volume: 1.5× = 0.5, 2.0× = 0.75, 3.0× = 1.0
            vol_score = min(1.0, (volume_ratio - 1.0) / 2.0)

        # ── 4. Detectar direção do breakout ──────────────────────────────────
        long_breakout_pct  = (current_price - resistance) / resistance
        short_breakout_pct = (support - current_price) / support

        direction = "NONE"
        breakout_pct = 0.0

        if long_breakout_pct >= BreakoutAnalyzer.MIN_BREAKOUT_PCT:
            direction    = "LONG"
            breakout_pct = long_breakout_pct
        elif short_breakout_pct >= BreakoutAnalyzer.MIN_BREAKOUT_PCT:
            direction    = "SHORT"
            breakout_pct = short_breakout_pct

        if direction == "NONE":
            return {**empty, "valid": True, "resistance": float(resistance),
                    "support": float(support), "atr_pct": float(atr_pct),
                    "volume_ratio": float(volume_ratio), "current_price": float(current_price)}

        # ── 5. Score de distância (quão longe do nível rompido) ──────────────
        # Normalizado por ATR; um rompimento de 1 ATR = score 0.5, de 2 ATR = score 1.0
        if atr_pct > 0:
            distance_score = min(1.0, breakout_pct / (atr_pct * 2.0))
        else:
            # Sem ATR: usa rompimento absoluto — 1% = 0.5, 2% = 1.0
            distance_score = min(1.0, breakout_pct / 0.02)

        # ── 6. Momentum pós-breakout (ROC confirma a direção) ────────────────
        roc_short = _roc(prices, period=3)
        roc_med   = _roc(prices, period=7)

        momentum_score_raw = 0.0
        if direction == "LONG":
            if roc_short > BreakoutAnalyzer.MIN_ROC_CONFIRM:
                momentum_score_raw = min(1.0, roc_short / 0.02)
            elif roc_short > 0:
                momentum_score_raw = 0.3
        elif direction == "SHORT":
            if roc_short < -BreakoutAnalyzer.MIN_ROC_CONFIRM:
                momentum_score_raw = min(1.0, abs(roc_short) / 0.02)
            elif roc_short < 0:
                momentum_score_raw = 0.3

        # Boost se ambos ROC curto e médio confirmam
        if direction == "LONG" and roc_med > 0 and roc_short > 0:
            momentum_score_raw = min(1.0, momentum_score_raw * 1.2)
        elif direction == "SHORT" and roc_med < 0 and roc_short < 0:
            momentum_score_raw = min(1.0, momentum_score_raw * 1.2)

        # ── 7. Score final ponderado ──────────────────────────────────────────
        # Volume é o mais importante: breakout sem volume = armadilha
        # Distância confirma a força do rompimento
        # Momentum valida que o movimento continua
        raw_score = (
            0.45 * vol_score           # volume é rei no breakout
            + 0.30 * distance_score    # distância do nível rompido
            + 0.25 * momentum_score_raw  # confirmação de direção via ROC
        )

        # Penalidade se volume não confirma (sem volume, breakout não é confiável)
        if volume_ratio < BreakoutAnalyzer.VOL_MULTIPLIER:
            raw_score *= 0.30  # penalidade severa: volume baixo = falso breakout comum

        breakout_score = round(raw_score, 4)
        entry_valid    = breakout_score >= BreakoutAnalyzer.BO_THRESHOLD

        return {
            "breakout_score":      breakout_score,
            "entry_valid":         entry_valid,
            "valid":               True,
            "direction":           direction,
            "resistance":          float(resistance),
            "support":             float(support),
            "atr_pct":             float(atr_pct),
            "volume_ratio":        float(volume_ratio),
            "breakout_pct":        float(breakout_pct),
            "vol_score":           float(vol_score),
            "distance_score":      float(distance_score),
            "momentum_score_raw":  float(momentum_score_raw),
            "current_price":       float(current_price),
        }

    @staticmethod
    def calculate_multiple_assets(
        assets_data: Dict[str, Dict],
        top_n: int = 1,
    ) -> Dict[str, Dict]:
        """
        Analisa todos os ativos e retorna os top_n candidatos para breakout.

        Args:
            assets_data: {asset: {prices: [...], volumes: [...]}}
            top_n: máx de candidatos retornados (padrão 1 — opera só o melhor)

        Returns:
            Dict {asset: resultado} dos candidatos válidos, ordenados por breakout_score desc.
        """
        results = {}
        for asset, data in assets_data.items():
            prices  = data.get("prices", [])
            volumes = data.get("volumes", [])
            result  = BreakoutAnalyzer.calculate_breakout_score(prices, volumes)
            if result["entry_valid"]:
                results[asset] = result

        # Ordenar por score e limitar a top_n
        sorted_results = sorted(
            results.items(),
            key=lambda x: x[1]["breakout_score"],
            reverse=True
        )
        return dict(sorted_results[:top_n])
