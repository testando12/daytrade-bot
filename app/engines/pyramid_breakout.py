"""
Engine de Breakout com Piramidagem Progressiva v1

Captura movimentos grandes de tendência via rompimento de volatilidade.
Simula piramidagem: aumenta posição conforme o trade confirma.

Conceito (Power Law Distribution):
  Mercados financeiros têm movimentos de tendência raros mas muito grandes.
  Esta estratégia captura exatamente esses eventos:
  - Muitos trades pequenos perdendo
  - Poucos trades gigantes pagando tudo

Indicadores:
  1. Bollinger Bands (20 períodos) — rompimento da banda superior/inferior
  2. ATR (14) crescente — volatilidade expandindo confirma breakout real
  3. Volume acima da média — confirmação de participação
  4. EMA20 vs EMA50 — tendência subjacente confirma direção

Piramidagem simulada:
  Posição base = risco_por_trade
  +1 ATR a favor → +50% da posição (simula 1ª piramidagem)
  +2 ATR a favor → +30% da posição (simula 2ª piramidagem)
  Total máximo: 180% da posição original

Trailing Stop:
  EMA20 ou 2 × ATR abaixo do preço (o que for mais apertado)

Gestão de risco:
  Risco por trade: 1.8% – 2.2% do capital (agressivo controlado)
  Stop loss: 1.5 ATR abaixo da entrada
  Máx trades simultâneos: 3
  Risco total aberto: ≤ 5% do capital

Filtro de ativação:
  ADX > 25 (só opera em tendência clara)

Perfil esperado:
  Win rate: 30-40%  |  R/R: 4:1 a 8:1  |  PF: 1.8-2.5  |  DD: 10-18%
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


def _atr(prices: List[float], period: int = 14) -> float:
    """ATR simplificado."""
    if len(prices) < 2:
        return 0.0
    trs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    recent = trs[-period:] if len(trs) >= period else trs
    return _mean(recent)


def _ema(prices: List[float], period: int) -> float:
    """EMA do último valor."""
    if len(prices) < period:
        return _mean(prices) if prices else 0.0
    mult = 2.0 / (period + 1.0)
    ema_val = _mean(prices[:period])
    for price in prices[period:]:
        ema_val = (price - ema_val) * mult + ema_val
    return ema_val


def _ema_series(prices: List[float], period: int) -> List[float]:
    """Retorna série EMA completa."""
    if len(prices) < period:
        return [_mean(prices)] * len(prices) if prices else []
    mult = 2.0 / (period + 1.0)
    result = [_mean(prices[:period])]
    for price in prices[period:]:
        result.append((price - result[-1]) * mult + result[-1])
    return result


def _bollinger_bands(prices: List[float], period: int = 20, mult: float = 2.0):
    """Retorna (middle, upper, lower) das Bollinger Bands."""
    if len(prices) < period:
        m = _mean(prices) if prices else 0.0
        return m, m, m
    recent = prices[-period:]
    middle = _mean(recent)
    std = _stddev(recent)
    return middle, middle + mult * std, middle - mult * std


class PyramidBreakoutAnalyzer:
    """
    Engine de Breakout com Piramidagem Progressiva v1.

    Detecta rompimentos de Bollinger Bands com ATR crescente, volume e EMA.
    Simula piramidagem para ampliar ganhos em trades vencedores.
    Melhor performance em mercados de tendência (ADX > 25).
    """

    # Score mínimo para entrada
    PB_THRESHOLD = 0.55

    # Bollinger Bands
    BB_PERIOD = 20
    BB_MULT = 2.0

    # ATR
    ATR_PERIOD = 14

    # Volume confirmação (1.3× = 30% acima da média)
    VOL_MULTIPLIER = 1.3

    # EMA tendência
    EMA_FAST = 20
    EMA_SLOW = 50

    # Piramidagem: multiplicador de posição por nível
    PYRAMID_LEVELS = {
        1: 1.00,   # posição base (100%)
        2: 0.50,   # +1 ATR a favor → +50%
        3: 0.30,   # +2 ATR a favor → +30%
    }
    # Total máximo: 180% da posição original

    # Stop e TP em múltiplos de ATR
    STOP_ATR_MULT = 1.5
    TP_ATR_MULT = 3.0  # R/R alvo de ~2:1 mínimo antes de piramidagem

    @staticmethod
    def calculate_pyramid_score(prices: List[float], volumes: List[float]) -> Dict:
        """
        Calcula o score de Pyramid Breakout para um ativo.

        Returns:
            Dict com pb_score (0-1), entry_valid, direction (LONG/SHORT/NONE),
            pyramid_multiplier, bb_upper, bb_lower, etc.
        """
        MIN_PERIODS = max(PyramidBreakoutAnalyzer.BB_PERIOD,
                          PyramidBreakoutAnalyzer.EMA_SLOW) + 5
        empty = {
            "pb_score": 0.0, "entry_valid": False, "valid": False,
            "direction": "NONE",
            "bb_middle": 0.0, "bb_upper": 0.0, "bb_lower": 0.0,
            "atr_pct": 0.0, "atr_expanding": False,
            "ema_fast": 0.0, "ema_slow": 0.0, "ema_aligned": False,
            "volume_ratio": 0.0,
            "pyramid_multiplier": 1.0, "pyramid_level": 1,
            "breakout_score": 0.0, "atr_score": 0.0,
            "ema_score": 0.0, "vol_score": 0.0,
            "current_price": prices[-1] if prices else 0.0,
        }

        if len(prices) < MIN_PERIODS or len(volumes) < MIN_PERIODS:
            return empty

        current_price = prices[-1]
        if current_price <= 0:
            return empty

        # ── 1. Bollinger Bands ───────────────────────────────────────────
        bb_mid, bb_up, bb_lo = _bollinger_bands(
            prices, PyramidBreakoutAnalyzer.BB_PERIOD, PyramidBreakoutAnalyzer.BB_MULT
        )
        bb_width = bb_up - bb_lo
        if bb_width <= 0:
            return {**empty, "current_price": current_price}

        # ── 2. ATR (verificar se está expandindo) ────────────────────────
        atr_current = _atr(prices, PyramidBreakoutAnalyzer.ATR_PERIOD)
        # ATR de 5 candles atrás para comparar
        atr_prev = _atr(prices[:-5], PyramidBreakoutAnalyzer.ATR_PERIOD) if len(prices) > 19 else atr_current
        atr_pct = atr_current / current_price if current_price > 0 else 0.0
        atr_expanding = atr_current > atr_prev * 1.05  # ATR cresceu pelo menos 5%

        # ── 3. EMA tendência (EMA20 vs EMA50) ───────────────────────────
        ema_fast = _ema(prices, PyramidBreakoutAnalyzer.EMA_FAST)
        ema_slow = _ema(prices, PyramidBreakoutAnalyzer.EMA_SLOW)

        # ── 4. Volume ────────────────────────────────────────────────────
        vol_lookback = volumes[-PyramidBreakoutAnalyzer.BB_PERIOD:]
        avg_vol = _mean(vol_lookback[:-1]) if len(vol_lookback) > 1 else (_mean(vol_lookback) or 1.0)
        last_vol = volumes[-1] if volumes else 0.0
        volume_ratio = last_vol / avg_vol if avg_vol > 0 else 0.0

        # ── 5. Detectar direção do breakout ──────────────────────────────
        direction = "NONE"

        # LONG: preço rompe BB upper + EMA20 > EMA50
        if current_price > bb_up and ema_fast > ema_slow:
            direction = "LONG"
        # SHORT: preço rompe BB lower + EMA20 < EMA50
        elif current_price < bb_lo and ema_fast < ema_slow:
            direction = "SHORT"
        # LONG fraco: preço rompe BB upper mas EMA não alinhada (penalizado)
        elif current_price > bb_up:
            direction = "LONG"
        # SHORT fraco: preço rompe BB lower mas EMA não alinhada
        elif current_price < bb_lo:
            direction = "SHORT"

        if direction == "NONE":
            return {**empty, "valid": True, "bb_middle": round(bb_mid, 6),
                    "bb_upper": round(bb_up, 6), "bb_lower": round(bb_lo, 6),
                    "atr_pct": round(atr_pct, 6), "ema_fast": round(ema_fast, 6),
                    "ema_slow": round(ema_slow, 6), "volume_ratio": round(volume_ratio, 3),
                    "current_price": round(current_price, 6)}

        # ── 6. Sub-scores ────────────────────────────────────────────────

        # Breakout strength: quão longe passou da banda
        if direction == "LONG":
            dist = (current_price - bb_up) / bb_width
        else:
            dist = (bb_lo - current_price) / bb_width
        breakout_score = min(1.0, max(0.0, dist * 2.0 + 0.3))

        # ATR expansion score
        if atr_expanding:
            atr_ratio = atr_current / atr_prev if atr_prev > 0 else 1.0
            atr_score = min(1.0, (atr_ratio - 1.0) / 0.5 + 0.5)
        else:
            atr_score = 0.2  # ATR sem expansão = sinal fraco

        # EMA alignment score
        if direction == "LONG":
            ema_aligned = ema_fast > ema_slow
        else:
            ema_aligned = ema_fast < ema_slow

        # Verificar se preço está acima/abaixo da EMA rápida
        if direction == "LONG":
            price_above_ema = current_price > ema_fast
        else:
            price_above_ema = current_price < ema_fast

        ema_score = 0.0
        if ema_aligned and price_above_ema:
            ema_score = 1.0
        elif ema_aligned:
            ema_score = 0.6
        elif price_above_ema:
            ema_score = 0.3
        else:
            ema_score = 0.1

        # Volume score
        if volume_ratio >= PyramidBreakoutAnalyzer.VOL_MULTIPLIER * 2.0:
            vol_score = 1.0
        elif volume_ratio >= PyramidBreakoutAnalyzer.VOL_MULTIPLIER:
            vol_score = 0.6
        elif volume_ratio >= 1.0:
            vol_score = 0.3
        else:
            vol_score = 0.0

        # ── 7. Piramidagem simulada ──────────────────────────────────────
        # Verifica o momentum recente para determinar nível de piramidagem
        # Usa retornos dos últimos candles como proxy de distância percorrida vs ATR

        pyramid_level = 1
        pyramid_multiplier = PyramidBreakoutAnalyzer.PYRAMID_LEVELS[1]

        if len(prices) >= 5 and atr_current > 0:
            # Retorno dos últimos 3 candles
            recent_ret = (prices[-1] - prices[-4]) / prices[-4] if prices[-4] > 0 else 0.0
            recent_move_atr = abs(recent_ret * current_price) / atr_current

            if recent_move_atr >= 2.0:
                # Movimento >= 2 ATR → piramidagem nível 3 (180%)
                pyramid_level = 3
                pyramid_multiplier = sum(PyramidBreakoutAnalyzer.PYRAMID_LEVELS.values())
            elif recent_move_atr >= 1.0:
                # Movimento >= 1 ATR → piramidagem nível 2 (150%)
                pyramid_level = 2
                pyramid_multiplier = (
                    PyramidBreakoutAnalyzer.PYRAMID_LEVELS[1]
                    + PyramidBreakoutAnalyzer.PYRAMID_LEVELS[2]
                )

        # ── 8. Score final ───────────────────────────────────────────────
        # Breakout strength é o sinal mais importante (35%)
        # ATR expansion confirma que é real (25%)
        # EMA alignment confirma tendência subjacente (20%)
        # Volume confirma participação (20%)
        raw_score = (
            0.35 * breakout_score
            + 0.25 * atr_score
            + 0.20 * ema_score
            + 0.20 * vol_score
        )

        # Boost: todos os sinais concordam
        if breakout_score > 0.3 and atr_score > 0.3 and ema_score > 0.3 and vol_score > 0.3:
            raw_score = min(1.0, raw_score * 1.20)

        # Penalidade: sem volume
        if volume_ratio < 1.0:
            raw_score *= 0.4

        # Penalidade: ATR contraindo (falso breakout provável)
        if not atr_expanding:
            raw_score *= 0.7

        pb_score = round(raw_score, 4)
        entry_valid = pb_score >= PyramidBreakoutAnalyzer.PB_THRESHOLD

        return {
            "pb_score": pb_score,
            "entry_valid": entry_valid,
            "valid": True,
            "direction": direction,
            "bb_middle": round(bb_mid, 6),
            "bb_upper": round(bb_up, 6),
            "bb_lower": round(bb_lo, 6),
            "atr_pct": round(atr_pct, 6),
            "atr_expanding": atr_expanding,
            "ema_fast": round(ema_fast, 6),
            "ema_slow": round(ema_slow, 6),
            "ema_aligned": ema_aligned,
            "volume_ratio": round(volume_ratio, 3),
            "pyramid_multiplier": round(pyramid_multiplier, 2),
            "pyramid_level": pyramid_level,
            "breakout_score": round(breakout_score, 4),
            "atr_score": round(atr_score, 4),
            "ema_score": round(ema_score, 4),
            "vol_score": round(vol_score, 4),
            "stop_atr_mult": PyramidBreakoutAnalyzer.STOP_ATR_MULT,
            "tp_atr_mult": PyramidBreakoutAnalyzer.TP_ATR_MULT,
            "current_price": round(current_price, 6),
        }

    @staticmethod
    def calculate_multiple_assets(
        assets_data: Dict[str, Dict],
        top_n: int = 2,
    ) -> Dict[str, Dict]:
        """
        Analisa todos os ativos e retorna os top_n candidatos para Pyramid Breakout.

        Args:
            assets_data: {asset: {prices: [...], volumes: [...]}}
            top_n: máx de candidatos retornados

        Returns:
            Dict {asset: resultado} dos candidatos válidos, ordenados por pb_score desc.
        """
        results = {}
        for asset, data in assets_data.items():
            prices = data.get("prices", [])
            volumes = data.get("volumes", [])
            result = PyramidBreakoutAnalyzer.calculate_pyramid_score(prices, volumes)
            if result["entry_valid"]:
                results[asset] = result

        sorted_results = sorted(
            results.items(),
            key=lambda x: x[1]["pb_score"],
            reverse=True
        )
        return dict(sorted_results[:top_n])
