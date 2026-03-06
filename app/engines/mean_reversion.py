"""
Engine de Mean Reversion v1

Detecta ativos em condição de oversold REAL com alta probabilidade de reversão.
Não pega facas caindo — só entra quando múltiplos indicadores confirmam esgotamento.

Indicadores:
  1. Bollinger Bands (20 períodos) — preço abaixo da banda inferior
  2. RSI oversold — RSI < 30 sinal normal / < 25 sinal forte
  3. Divergência de momentum — preço cai mas ROC curto melhora (desaceleração da queda)
  4. Volume de exaustão — volume alto no candle de queda = vendedores se esgotando
  5. Z-Score — quão longe em desvios-padrão está da média

Só considera entrada válida quando:
  - Pelo menos 2 dos 4 indicadores confirmam
  - MR score total >= MR_THRESHOLD (0.55)
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
    """Engine de Mean Reversion v1 — detecta reversões de oversold com alta confiança."""

    # Score mínimo para considerar entrada válida
    MR_THRESHOLD = 0.55

    # Bollinger Bands
    BB_PERIOD  = 20
    BB_STD_DEV = 2.0

    # RSI thresholds
    RSI_OVERSOLD_STRONG = 25  # score máximo
    RSI_OVERSOLD_NORMAL = 32  # score moderado

    # Exige no mínimo este número de sinais confirmando
    MIN_SIGNALS_REQUIRED = 2

    @staticmethod
    def calculate_mr_score(prices: List[float], volumes: List[float]) -> Dict:
        """
        Calcula o score de mean reversion para um ativo.

        Returns:
            Dict com mr_score (0-1), entry_valid, rsi, z_score,
            bb_position, signal_type e sub-scores.
        """
        MIN_PERIODS = 22
        empty = {
            "mr_score": 0.0, "entry_valid": False, "valid": False,
            "rsi": 50.0, "z_score": 0.0, "bb_position": 0.0,
            "signal_type": "NEUTRAL", "current_price": prices[-1] if prices else 0.0,
            "bb_score": 0.0, "rsi_score": 0.0,
            "divergence_score": 0.0, "vol_score": 0.0,
        }
        if len(prices) < MIN_PERIODS:
            return empty

        price  = prices[-1]
        period = min(MeanReversionAnalyzer.BB_PERIOD, len(prices))
        recent = prices[-period:]

        # ── 1. Bollinger Bands ────────────────────────────────────────────
        bb_mean   = _mean(recent)
        bb_std    = _stddev(recent)
        bb_upper  = bb_mean + MeanReversionAnalyzer.BB_STD_DEV * bb_std
        bb_lower  = bb_mean - MeanReversionAnalyzer.BB_STD_DEV * bb_std
        half_band = (bb_upper - bb_lower) / 2.0 if (bb_upper - bb_lower) > 0 else 1e-9

        # Z-score: desvios-padrão abaixo da média
        z_score = (price - bb_mean) / bb_std if bb_std > 0 else 0.0

        if price < bb_lower:
            # Quanto mais abaixo da banda inferior, maior o score (max em 2.5× banda)
            depth = (bb_lower - price) / (bb_std + 1e-9)
            bb_score = min(1.0, depth / 1.5)
        else:
            bb_score = 0.0

        # ── 2. RSI oversold ───────────────────────────────────────────────
        rsi = _rsi(prices, min(14, len(prices) - 2))
        if rsi <= MeanReversionAnalyzer.RSI_OVERSOLD_STRONG:
            rsi_score = 1.0
        elif rsi <= MeanReversionAnalyzer.RSI_OVERSOLD_NORMAL:
            span = MeanReversionAnalyzer.RSI_OVERSOLD_NORMAL - MeanReversionAnalyzer.RSI_OVERSOLD_STRONG
            rsi_score = (MeanReversionAnalyzer.RSI_OVERSOLD_NORMAL - rsi) / span
        else:
            rsi_score = 0.0

        # ── 3. Divergência: queda desacelerando ───────────────────────────
        divergence_score = 0.0
        if len(prices) >= 10:
            # ROC dos últimos 3 candles vs 3-7 candles atrás
            roc_recent = (prices[-1] - prices[-4]) / prices[-4] if prices[-4] != 0 else 0.0
            roc_prev   = (prices[-4] - prices[-8]) / prices[-8] if len(prices) >= 9 and prices[-8] != 0 else roc_recent
            # Divergência bullish: preço ainda cai (roc_recent < 0) mas a taxa de queda desacelera
            if roc_recent < 0 and roc_prev < roc_recent:
                # Queda está desacelerando — quanto mais a desaceleração, maior o score
                deceleration = abs(roc_prev - roc_recent)
                divergence_score = min(1.0, deceleration / max(abs(roc_prev), 1e-6))

        # ── 4. Volume de exaustão vendedora ───────────────────────────────
        vol_score = 0.0
        if len(volumes) >= 10 and len(prices) >= 2:
            avg_vol   = _mean(volumes[-10:])
            last_vol  = volumes[-1]
            price_drop = prices[-1] < prices[-2]  # candle de queda
            if avg_vol > 0 and price_drop:
                # Volume >1.5× a média em um candle de queda = exaustão vendedora
                surge = last_vol / avg_vol
                vol_score = min(1.0, max(0.0, (surge - 1.0) / 2.5))

        # ── Score final ponderado ─────────────────────────────────────────
        # BB e RSI têm mais peso (evidência direta de oversold)
        raw_score = (
            0.35 * bb_score
            + 0.35 * rsi_score
            + 0.15 * divergence_score
            + 0.15 * vol_score
        )

        # Penaliza se menos de MIN_SIGNALS_REQUIRED indicadores confirmam
        active_signals = sum([
            bb_score > 0.25,
            rsi_score > 0.25,
            divergence_score > 0.20,
            vol_score > 0.15,
        ])
        if active_signals < MeanReversionAnalyzer.MIN_SIGNALS_REQUIRED:
            raw_score *= 0.40  # penalidade severa: leitura isolada não confiável

        mr_score = round(raw_score, 4)

        # Tipo de sinal
        if z_score <= -2.5 and rsi <= 30:
            signal_type = "OVERSOLD_STRONG"
        elif z_score <= -1.8 or rsi <= MeanReversionAnalyzer.RSI_OVERSOLD_NORMAL:
            signal_type = "OVERSOLD"
        elif z_score >= 1.8 or rsi >= 70:
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
            "bb_mean":           float(bb_mean),
            "bb_score":          float(bb_score),
            "rsi_score":         float(rsi_score),
            "divergence_score":  float(divergence_score),
            "vol_score":         float(vol_score),
            "active_signals":    int(active_signals),
            "signal_type":       signal_type,
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
