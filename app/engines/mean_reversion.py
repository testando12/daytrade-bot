"""
Engine de Mean Reversion v2 — Enhanced Bollinger Bands

Detecta ativos em condição de oversold/overbought REAL com alta probabilidade de reversão.
Não pega facas caindo — só entra quando múltiplos indicadores confirmam esgotamento.

v2.0 Melhorias:
  - BB com 2 bandas: padrão (2σ) e tight (1.5σ) — preço entre 1.5σ e 2σ = sinal fraco
  - BB %B indicator para posição relativa precisa
  - Overbought detection: vende quando preço > BB superior (short/exit signal)
  - BB Width Squeeze: quando BB estreita → iminente explosão direcional
  - RSI otimizado: zona de compra ≤35 (era ≤32), zona forte ≤25
  - Adaptive BB period: usa 15 em dados curtos, 20 em dados longos

Indicadores:
  1. Bollinger Bands (20 períodos, 2σ + 1.5σ) — posição relativa via %B
  2. RSI oversold/overbought — RSI < 35 buy / RSI > 65 sell
  3. Divergência de momentum — preço cai mas ROC curto melhora
  4. Volume de exaustão — volume alto no candle de queda = vendedores esgotados
  5. Z-Score — distância em desvios-padrão da média
  6. BB Width — detecção de squeeze/expansão
"""

from typing import Dict, List


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stddev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return (sum((v - m) ** 2 for v in values) / len(values)) ** 0.5


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


class MeanReversionAnalyzer:
    """Engine de Mean Reversion v2 — Enhanced Bollinger Bands + RSI + divergência."""

    # Score mínimo para considerar entrada válida
    MR_THRESHOLD = 0.52  # v2: lowered from 0.55 — BB enhancements compensam

    # Bollinger Bands — dual band system
    BB_PERIOD     = 20
    BB_STD_DEV    = 2.0    # banda padrão (sinal forte)
    BB_TIGHT_DEV  = 1.5    # banda tight (sinal moderado — zona de atenção)

    # RSI thresholds — v2: relaxados para gerar mais sinais em lateral
    RSI_OVERSOLD_STRONG  = 25   # score máximo buy
    RSI_OVERSOLD_NORMAL  = 35   # score moderado buy (era 32)
    RSI_OVERBOUGHT_STRONG = 75  # score máximo sell (NOVO)
    RSI_OVERBOUGHT_NORMAL = 65  # score moderado sell (NOVO)

    # BB Width Squeeze detection
    BB_WIDTH_SQUEEZE_THRESHOLD = 0.03  # largura < 3% do preço = squeeze

    # Exige no mínimo este número de sinais confirmando
    MIN_SIGNALS_REQUIRED = 2

    @staticmethod
    def calculate_mr_score(prices: List[float], volumes: List[float]) -> Dict:
        """
        Calcula o score de mean reversion para um ativo.
        v2: Enhanced com dual BB, overbought signals e BB width squeeze.

        Returns:
            Dict com mr_score (0-1), entry_valid, rsi, z_score,
            bb_position, signal_type, direction, bb_pct_b e sub-scores.
        """
        MIN_PERIODS = 22
        empty = {
            "mr_score": 0.0, "entry_valid": False, "valid": False,
            "rsi": 50.0, "z_score": 0.0, "bb_position": 0.0,
            "signal_type": "NEUTRAL", "direction": "neutral",
            "current_price": prices[-1] if prices else 0.0,
            "bb_score": 0.0, "rsi_score": 0.0,
            "divergence_score": 0.0, "vol_score": 0.0,
            "bb_pct_b": 0.5, "bb_width": 0.0, "bb_squeeze": False,
        }
        if len(prices) < MIN_PERIODS:
            return empty

        price  = prices[-1]
        period = min(MeanReversionAnalyzer.BB_PERIOD, len(prices))
        recent = prices[-period:]

        # ── 1. Dual Bollinger Bands ───────────────────────────────────────
        bb_mean   = _mean(recent)
        bb_std    = _stddev(recent)
        bb_upper  = bb_mean + MeanReversionAnalyzer.BB_STD_DEV * bb_std
        bb_lower  = bb_mean - MeanReversionAnalyzer.BB_STD_DEV * bb_std
        # Tight bands (1.5σ) — zona de atenção
        bb_upper_tight = bb_mean + MeanReversionAnalyzer.BB_TIGHT_DEV * bb_std
        bb_lower_tight = bb_mean - MeanReversionAnalyzer.BB_TIGHT_DEV * bb_std

        half_band = (bb_upper - bb_lower) / 2.0 if (bb_upper - bb_lower) > 0 else 1e-9

        # %B indicator: 0 = na banda inferior, 1 = na banda superior
        bb_range = bb_upper - bb_lower
        bb_pct_b = (price - bb_lower) / bb_range if bb_range > 0 else 0.5

        # BB Width: largura relativa ao preço (squeeze detection)
        bb_width = bb_range / bb_mean if bb_mean > 0 else 0.0
        bb_squeeze = bb_width < MeanReversionAnalyzer.BB_WIDTH_SQUEEZE_THRESHOLD

        # Z-score: desvios-padrão da média
        z_score = (price - bb_mean) / bb_std if bb_std > 0 else 0.0

        # ── BB Score (oversold: abaixo de BB lower) ─────────────────
        bb_score = 0.0
        direction = "neutral"
        if price < bb_lower:
            # Forte: abaixo da banda 2σ
            depth = (bb_lower - price) / (bb_std + 1e-9)
            bb_score = min(1.0, depth / 1.5)
            direction = "buy"
        elif price < bb_lower_tight:
            # Moderado: entre 1.5σ e 2σ abaixo
            depth = (bb_lower_tight - price) / (bb_std + 1e-9)
            bb_score = min(0.50, depth / 1.0) * 0.60  # pontuação parcial
            direction = "buy"
        elif price > bb_upper:
            # Overbought: acima da banda 2σ
            depth = (price - bb_upper) / (bb_std + 1e-9)
            bb_score = min(1.0, depth / 1.5)
            direction = "sell"
        elif price > bb_upper_tight:
            # Moderado overbought: entre 1.5σ e 2σ acima
            depth = (price - bb_upper_tight) / (bb_std + 1e-9)
            bb_score = min(0.45, depth / 1.0) * 0.50
            direction = "sell"

        # ── 2. RSI oversold/overbought ────────────────────────────────────
        rsi = _rsi(prices, min(14, len(prices) - 2))
        rsi_score = 0.0
        if rsi <= MeanReversionAnalyzer.RSI_OVERSOLD_STRONG:
            rsi_score = 1.0
            if direction != "sell":
                direction = "buy"
        elif rsi <= MeanReversionAnalyzer.RSI_OVERSOLD_NORMAL:
            span = MeanReversionAnalyzer.RSI_OVERSOLD_NORMAL - MeanReversionAnalyzer.RSI_OVERSOLD_STRONG
            rsi_score = (MeanReversionAnalyzer.RSI_OVERSOLD_NORMAL - rsi) / span
            if direction != "sell":
                direction = "buy"
        elif rsi >= MeanReversionAnalyzer.RSI_OVERBOUGHT_STRONG:
            rsi_score = 1.0
            if direction != "buy":
                direction = "sell"
        elif rsi >= MeanReversionAnalyzer.RSI_OVERBOUGHT_NORMAL:
            span = MeanReversionAnalyzer.RSI_OVERBOUGHT_STRONG - MeanReversionAnalyzer.RSI_OVERBOUGHT_NORMAL
            rsi_score = (rsi - MeanReversionAnalyzer.RSI_OVERBOUGHT_NORMAL) / span
            if direction != "buy":
                direction = "sell"

        # ── 3. Divergência: queda/subida desacelerando ────────────────────
        divergence_score = 0.0
        if len(prices) >= 10:
            roc_recent = (prices[-1] - prices[-4]) / prices[-4] if prices[-4] != 0 else 0.0
            roc_prev   = (prices[-4] - prices[-8]) / prices[-8] if len(prices) >= 9 and prices[-8] != 0 else roc_recent
            if direction == "buy" and roc_recent < 0 and roc_prev < roc_recent:
                # Queda desacelerando → bullish divergence
                deceleration = abs(roc_prev - roc_recent)
                divergence_score = min(1.0, deceleration / max(abs(roc_prev), 1e-6))
            elif direction == "sell" and roc_recent > 0 and roc_prev > roc_recent:
                # Subida desacelerando → bearish divergence
                deceleration = abs(roc_prev - roc_recent)
                divergence_score = min(1.0, deceleration / max(abs(roc_prev), 1e-6))

        # ── 4. Volume de exaustão ─────────────────────────────────────────
        vol_score = 0.0
        if len(volumes) >= 10 and len(prices) >= 2:
            avg_vol   = _mean(volumes[-10:])
            last_vol  = volumes[-1]
            price_drop = prices[-1] < prices[-2]
            price_rise = prices[-1] > prices[-2]
            if avg_vol > 0:
                if price_drop and direction == "buy":
                    surge = last_vol / avg_vol
                    vol_score = min(1.0, max(0.0, (surge - 1.0) / 2.5))
                elif price_rise and direction == "sell":
                    surge = last_vol / avg_vol
                    vol_score = min(1.0, max(0.0, (surge - 1.0) / 2.5))

        # ── 5. BB Squeeze bonus — se BB estreita, reversão é mais explosiva ──
        squeeze_bonus = 0.0
        if bb_squeeze and (direction == "buy" or direction == "sell"):
            squeeze_bonus = 0.12  # +12% bonus ao score final

        # ── Score final ponderado ─────────────────────────────────────────
        raw_score = (
            0.30 * bb_score
            + 0.30 * rsi_score
            + 0.15 * divergence_score
            + 0.15 * vol_score
            + squeeze_bonus
        )

        # Penaliza se menos de MIN_SIGNALS_REQUIRED indicadores confirmam
        active_signals = sum([
            bb_score > 0.20,
            rsi_score > 0.20,
            divergence_score > 0.15,
            vol_score > 0.10,
        ])
        if active_signals < MeanReversionAnalyzer.MIN_SIGNALS_REQUIRED:
            raw_score *= 0.40

        mr_score = round(raw_score, 4)

        # Tipo de sinal
        if z_score <= -2.5 and rsi <= 30:
            signal_type = "OVERSOLD_STRONG"
        elif z_score <= -1.8 or rsi <= MeanReversionAnalyzer.RSI_OVERSOLD_NORMAL:
            signal_type = "OVERSOLD"
        elif z_score >= 2.5 and rsi >= 70:
            signal_type = "OVERBOUGHT_STRONG"
        elif z_score >= 1.8 or rsi >= MeanReversionAnalyzer.RSI_OVERBOUGHT_NORMAL:
            signal_type = "OVERBOUGHT"
        else:
            signal_type = "NEUTRAL"

        entry_valid = mr_score >= MeanReversionAnalyzer.MR_THRESHOLD

        return {
            "mr_score":          mr_score,
            "entry_valid":       entry_valid,
            "valid":             True,
            "rsi":               float(rsi),
            "z_score":           float(z_score),
            "bb_position":       float((price - bb_mean) / half_band) if half_band > 0 else 0.0,
            "bb_lower":          float(bb_lower),
            "bb_upper":          float(bb_upper),
            "bb_lower_tight":    float(bb_lower_tight),
            "bb_upper_tight":    float(bb_upper_tight),
            "bb_mean":           float(bb_mean),
            "bb_pct_b":          round(float(bb_pct_b), 4),
            "bb_width":          round(float(bb_width), 4),
            "bb_squeeze":        bb_squeeze,
            "bb_score":          float(bb_score),
            "rsi_score":         float(rsi_score),
            "divergence_score":  float(divergence_score),
            "vol_score":         float(vol_score),
            "squeeze_bonus":     float(squeeze_bonus),
            "active_signals":    int(active_signals),
            "signal_type":       signal_type,
            "direction":         direction,
            "current_price":     float(price),
        }

    @staticmethod
    def calculate_multiple_assets(
        assets_data: Dict[str, Dict],
        top_n: int = 2,
    ) -> Dict[str, Dict]:
        """
        Analisa todos os ativos e retorna os top_n candidatos para mean reversion.

        Args:
            assets_data: {asset: {prices: [...], volumes: [...]}}
            top_n: máx de candidatos retornados

        Returns:
            Dict {asset: resultado} dos candidatos válidos, ordenados por mr_score desc.
        """
        results = {}
        for asset, data in assets_data.items():
            prices  = data.get("prices", [])
            volumes = data.get("volumes", [])
            result  = MeanReversionAnalyzer.calculate_mr_score(prices, volumes)
            if result["entry_valid"]:
                results[asset] = result

        # Ordenar por score e limitar a top_n
        sorted_results = sorted(results.items(), key=lambda x: x[1]["mr_score"], reverse=True)
        return dict(sorted_results[:top_n])
