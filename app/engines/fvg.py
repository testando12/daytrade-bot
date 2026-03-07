"""
Fair Value Gap (FVG) Engine v1  — Gaps Institucionais / Imbalance Zones

FVG é uma zona de desequilíbrio de preço criada quando o mercado se move
tão rápido que deixa uma "lacuna" de preço que não foi negociada.

Padrão de 3 candles:
  Bullish FVG:
    candle[-3].high  < candle[-1].low     → gap não coberto entre os dois
    → zona de suporte; preço tende a voltar e "cobrir" esse gap → LONG fill

  Bearish FVG:
    candle[-3].low   > candle[-1].high    → gap não coberto
    → zona de resistência; preço tende a voltar e "cobrir" → SHORT fill

Como só temos dados de fechamento (close), usamos a variação ROC do candle central
comparada com ATR para inferir que um "impulse candle" ocorreu.

Aproximação com close-only:
  gap_up   (bullish FVG):
    prices[-3] << prices[-2] >> prices[-1]  (impulso para cima, retração parcial)
    O impulso foi >= 1.5× ATR:
      retração (-) = prices[-1] < prices[-2]
      o preço atual (prices[-1]) caiu mas ainda está acima de prices[-3]
      → Zona de "fill" = intervalo (prices[-3], prices[-1])

  gap_down (bearish FVG):
    prices[-3] >> prices[-2] << prices[-1]  (impulso para baixo, retração parcial)
    O impulso descendente foi >= 1.5× ATR:
      o preço atual (prices[-1]) subiu mas ainda está abaixo de prices[-3]
      → Zona de "fill" = intervalo (prices[-1], prices[-3])

Score final 0-1; entry_valid quando score >= 0.60
"""

from typing import Dict, List
import math


def _mean(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _atr_simple(prices: List[float], period: int = 10) -> float:
    if len(prices) < 2:
        return 0.0
    trs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    recent = trs[-period:] if len(trs) >= period else trs
    return _mean(recent)


def _std(vals: List[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((x - m) ** 2 for x in vals) / len(vals))


class FVGAnalyzer:
    """Engine de Fair Value Gap v1 — detecta gaps institucionais e trades de fill."""

    FVG_THRESHOLD    = 0.60   # score mínimo para entry_valid
    LOOKBACK         = 30     # período para calcular ATR e contexto
    MIN_GAP_ATR_MULT = 1.2    # impulso mínimo = 1.2× ATR para ser FVG válido
    MAX_RETRACEMENT  = 0.70   # o preço não pode já ter coberto > 70% do gap

    @staticmethod
    def calculate_fvg_score(prices: List[float], volumes: List[float]) -> Dict:
        """
        Calcula o score de FVG para um ativo.

        Returns:
            Dict com fvg_score (0-1), entry_valid, direction (LONG/SHORT/NONE),
            gap_size_pct, fill_pct, fvg_zone (tuple) e sub-scores.
        """
        MIN_PERIODS = FVGAnalyzer.LOOKBACK + 5
        empty = {
            "fvg_score": 0.0, "entry_valid": False, "valid": False,
            "direction": "NONE", "gap_size_pct": 0.0, "fill_pct": 0.0,
            "fvg_zone": (0.0, 0.0),
            "current_price": prices[-1] if prices else 0.0,
        }
        if len(prices) < MIN_PERIODS:
            return empty

        current_price = prices[-1]
        if current_price <= 0:
            return empty

        # ATR para calibrar o "impulso mínimo"
        context_prices = prices[-(FVGAnalyzer.LOOKBACK + 3):]
        atr = _atr_simple(context_prices, period=10)
        if atr <= 0:
            return empty

        # ── Procurar FVG dentro do lookback ──────────────────────────────
        # Varre os últimos N candles procurando o FVG mais recente com maior gap
        best_direction = "NONE"
        best_gap_pct   = 0.0
        best_fill_pct  = 0.0
        best_zone      = (0.0, 0.0)
        best_impulse_mult = 0.0
        best_vol_ratio    = 1.0

        search_prices = prices[-(FVGAnalyzer.LOOKBACK + 2):]
        search_volumes = volumes[-(FVGAnalyzer.LOOKBACK + 2):] if len(volumes) >= FVGAnalyzer.LOOKBACK + 2 else volumes

        avg_vol = _mean(search_volumes) if search_volumes else 0.0

        for i in range(2, len(search_prices) - 1):
            p_before  = search_prices[i - 2]   # candle "A" (antes do impulso)
            p_impulse = search_prices[i - 1]   # candle "B" (impulso)
            p_after   = search_prices[i]        # candle "C" (retração parcial)

            if p_before <= 0 or p_impulse <= 0 or p_after <= 0:
                continue

            move_up   = p_impulse - p_before   # positivo = impulso de alta
            move_down = p_before  - p_impulse  # positivo = impulso de baixa

            # ── Bullish FVG: impulso de alta >>>>>>>>>>>>>>>>>>>>>>>>>>>>
            if move_up >= atr * FVGAnalyzer.MIN_GAP_ATR_MULT:
                # Gap zone: a zona não coberta é entre p_before e p_after
                # (se p_after < p_impulse, voltou parcialmente; gap = p_before → p_after)
                if p_after > p_before:  # parte do impulso ainda não coberta
                    gap_size = p_after - p_before
                    gap_pct  = gap_size / p_before

                    # Quanto do gap ainda não foi coberto
                    covered  = max(0.0, p_impulse - p_after) / max(move_up, 1e-10)
                    remaining = 1.0 - covered
                    if remaining < (1 - FVGAnalyzer.MAX_RETRACEMENT):
                        continue  # gap já muito coberto

                    impulse_mult = move_up / atr

                    # Verifica se o PREÇO ATUAL está dentro ou próximo da zona de fill
                    zone_low  = p_before
                    zone_high = p_after
                    if zone_low <= current_price <= zone_high * 1.01:
                        # Preço dentro da zona — fill trade LONG ativo
                        fill_pct = (current_price - zone_low) / (zone_high - zone_low) if (zone_high - zone_low) > 0 else 0.5
                        vol_idx = max(0, i - len(search_prices) + len(search_volumes))
                        imp_vol = search_volumes[vol_idx - 1] if vol_idx > 0 and vol_idx - 1 < len(search_volumes) else 0.0
                        vol_ratio = (imp_vol / avg_vol) if avg_vol > 0 else 1.0

                        if gap_pct > best_gap_pct:
                            best_direction    = "LONG"
                            best_gap_pct      = gap_pct
                            best_fill_pct     = fill_pct
                            best_zone         = (float(zone_low), float(zone_high))
                            best_impulse_mult = impulse_mult
                            best_vol_ratio    = vol_ratio

            # ── Bearish FVG: impulso de baixa >>>>>>>>>>>>>>>>>>>>>>>>>>>
            elif move_down >= atr * FVGAnalyzer.MIN_GAP_ATR_MULT:
                if p_after < p_before:  # parte do impulso de baixa ainda não coberta
                    gap_size = p_before - p_after
                    gap_pct  = gap_size / p_before

                    covered  = max(0.0, p_after - p_impulse) / max(move_down, 1e-10)
                    remaining = 1.0 - covered
                    if remaining < (1 - FVGAnalyzer.MAX_RETRACEMENT):
                        continue

                    impulse_mult = move_down / atr

                    zone_low  = p_after
                    zone_high = p_before
                    if zone_low * 0.99 <= current_price <= zone_high:
                        fill_pct = (zone_high - current_price) / (zone_high - zone_low) if (zone_high - zone_low) > 0 else 0.5
                        vol_idx = max(0, i - len(search_prices) + len(search_volumes))
                        imp_vol = search_volumes[vol_idx - 1] if vol_idx > 0 and vol_idx - 1 < len(search_volumes) else 0.0
                        vol_ratio = (imp_vol / avg_vol) if avg_vol > 0 else 1.0

                        if gap_pct > best_gap_pct:
                            best_direction    = "SHORT"
                            best_gap_pct      = gap_pct
                            best_fill_pct     = fill_pct
                            best_zone         = (float(zone_low), float(zone_high))
                            best_impulse_mult = impulse_mult
                            best_vol_ratio    = vol_ratio

        if best_direction == "NONE":
            return {**empty, "valid": True, "current_price": float(current_price)}

        # ── Score da magnitude do gap ─────────────────────────────────────
        # Gap de 1% = ~0.5, 2% = ~0.7, 3%+ = ~1.0
        gap_score = min(1.0, best_gap_pct / 0.025)

        # ── Score do posicionamento no gap (melhor quando perto do extremo) ─
        # LONG: melhor entrar no início do gap (fill_pct próximo de 0)
        # SHORT: melhor entrar quando preço ainda não subiu muito dentro do gap
        proximity_score = min(1.0, max(0.0, 1.0 - best_fill_pct))

        # ── Score do impulso (mais ATR = mais significativo) ──────────────
        impulse_score = min(1.0, (best_impulse_mult - FVGAnalyzer.MIN_GAP_ATR_MULT) / 3.0)

        # ── Volume no candle de impulso ───────────────────────────────────
        vol_score = min(1.0, max(0.0, (best_vol_ratio - 1.0) / 2.0))

        # ── Score final ponderado ─────────────────────────────────────────
        raw_score = (
            0.35 * gap_score           # tamanho do gap
            + 0.30 * proximity_score   # posicionamento dentro do gap
            + 0.20 * impulse_score     # força do impulso original
            + 0.15 * vol_score         # volume no impulso
        )

        fvg_score   = round(raw_score, 4)
        entry_valid = fvg_score >= FVGAnalyzer.FVG_THRESHOLD

        return {
            "fvg_score":      fvg_score,
            "entry_valid":    entry_valid,
            "valid":          True,
            "direction":      best_direction,
            "gap_size_pct":   round(best_gap_pct * 100, 3),
            "fill_pct":       round(best_fill_pct * 100, 1),
            "fvg_zone":       best_zone,
            "gap_score":      round(gap_score, 4),
            "proximity_score": round(proximity_score, 4),
            "impulse_score":  round(impulse_score, 4),
            "vol_score":      round(vol_score, 4),
            "current_price":  float(current_price),
        }

    @staticmethod
    def calculate_multiple_assets(
        assets_data: Dict[str, Dict],
        top_n: int = 1,
    ) -> Dict[str, Dict]:
        """
        Analisa todos os ativos e retorna os top_n candidatos com FVG ativo.

        Args:
            assets_data: {asset: {prices: [...], volumes: [...]}}
            top_n: máx de candidatos retornados (padrão 1)

        Returns:
            Dict {asset: resultado} ordenado por fvg_score desc.
        """
        results = {}
        for asset, data in assets_data.items():
            prices  = data.get("prices", [])
            volumes = data.get("volumes", [])
            result  = FVGAnalyzer.calculate_fvg_score(prices, volumes)
            if result["entry_valid"]:
                results[asset] = result

        sorted_results = sorted(
            results.items(),
            key=lambda x: x[1]["fvg_score"],
            reverse=True,
        )
        return dict(sorted_results[:top_n])
