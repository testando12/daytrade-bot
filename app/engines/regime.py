"""
Market Regime Detector v1  — Detecção Multi-Sinal de Regime de Mercado

Combina 3 sinais independentes para classificar o regime atual:

  1. ADX  (Average Directional Index simplificado)
     > 25 → tendência forte      < 18 → lateral
     Fonte: variações absolutas de fechamento como proxy de True Range

  2. ATR Ratio (volatilidade relativa)
     ATR_atual / ATR_média:
       > 1.5 → volatilidade alta   < 0.70 → volatilidade baixa

  3. Hurst Exponent (persistência de tendência)
     Usa análise R/S simplificada sobre os log-retornos:
       H > 0.55 → mercado com memória (trend-following favorecido)
       H < 0.45 → reversão à média (mean-reversion favorecida)
       H ≈ 0.50 → aleatório / neutro

Regime Final (combinação dos 3):
  TREND_STRONG  : ADX > 25  +  H > 0.52  → máx boost para Breakout/Momentum
  TREND_WEAK    : ADX > 20  +  H > 0.50  → boost leve para trend-following
  LATERAL       : ADX < 20  +  H < 0.52  → boost para MR/Squeeze, reduz Breakout
  HIGH_VOL      : ATR_ratio > 1.6        → reduz todos os tamanhos de posição
  LOW_VOL_SQUEEZE: ATR_ratio < 0.65      → boost para Squeeze (pré-expansão)
  NEUTRAL       : nenhum regime claro     → sem ajuste

Capital Multipliers por estratégia e regime:
  key = (regime, strategy) → multiplier float
"""

from typing import Dict, List, Tuple
import math


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _mean(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _atr_simple(prices: List[float], period: int = 14) -> float:
    if len(prices) < 2:
        return 0.0
    trs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    recent = trs[-period:] if len(trs) >= period else trs
    return _mean(recent)


def _calc_adx(prices: List[float], period: int = 14) -> float:
    """
    ADX simplificado usando apenas fechamentos.
    Retorna valor 0-100.
    """
    if len(prices) < period * 3:
        return 20.0
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    recent  = changes[-(period * 3):]

    def wilder(vals: List[float], p: int) -> float:
        if len(vals) < p:
            return sum(abs(v) for v in vals) / max(len(vals), 1)
        sm = sum(abs(v) for v in vals[:p])
        for v in vals[p:]:
            sm = sm - sm / p + abs(v)
        return sm

    plus_dm  = [max(c, 0.0) for c in recent]
    minus_dm = [max(-c, 0.0) for c in recent]
    tr_vals  = [abs(c) for c in recent]

    sm_plus  = wilder(plus_dm, period)
    sm_minus = wilder(minus_dm, period)
    sm_tr    = wilder(tr_vals, period)

    if sm_tr == 0:
        return 0.0
    plus_di  = 100.0 * sm_plus  / sm_tr
    minus_di = 100.0 * sm_minus / sm_tr
    denom    = plus_di + minus_di
    if denom == 0:
        return 0.0
    dx = 100.0 * abs(plus_di - minus_di) / denom
    return round(dx, 2)


def _calc_hurst(prices: List[float], min_len: int = 20) -> float:
    """
    Hurst Exponent via R/S simplificado.
    Retorna valor 0-1; 0.5 = aleatório, >0.55 = trend, <0.45 = mean-reversion.
    """
    if len(prices) < min_len:
        return 0.5

    # Log-retornos
    log_rets = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0 and prices[i] > 0:
            log_rets.append(math.log(prices[i] / prices[i - 1]))

    if len(log_rets) < min_len:
        return 0.5

    def rs_stat(series: List[float]) -> float:
        n = len(series)
        if n < 2:
            return 1.0
        mean_s = _mean(series)
        deviations = [x - mean_s for x in series]
        cumdev: List[float] = []
        cum = 0.0
        for d in deviations:
            cum += d
            cumdev.append(cum)
        R = max(cumdev) - min(cumdev)
        variance = sum(x ** 2 for x in deviations) / n
        S = math.sqrt(variance)
        return R / S if S > 0 else 1.0

    # R/S em múltiplas escalas para estimar H
    rs_vals: List[Tuple[float, float]] = []  # (log_n, log_RS)
    n_total = len(log_rets)

    for sub_len in [max(4, n_total // 4), max(4, n_total // 2), n_total]:
        if sub_len > n_total:
            continue
        chunk = log_rets[-sub_len:]
        rs = rs_stat(chunk)
        if rs > 0 and sub_len > 1:
            rs_vals.append((math.log(sub_len), math.log(rs)))

    if len(rs_vals) < 2:
        return 0.5

    # Regressão linear simples: log(RS) = H * log(n) + c → H = slope
    x_vals = [v[0] for v in rs_vals]
    y_vals = [v[1] for v in rs_vals]
    x_mean = _mean(x_vals)
    y_mean = _mean(y_vals)
    num    = sum((x_vals[i] - x_mean) * (y_vals[i] - y_mean) for i in range(len(x_vals)))
    den    = sum((x_vals[i] - x_mean) ** 2 for i in range(len(x_vals)))
    if den == 0:
        return 0.5
    hurst = num / den
    # Clamp razoável
    return round(max(0.1, min(0.9, hurst)), 3)


def _detect_direction(prices: List[float], short: int = 10, long: int = 30) -> str:
    """
    Detecta direção da tendência: 'up', 'down' ou 'neutral'.
    Usa SMA curta vs SMA longa + inclinação recente.
    """
    if len(prices) < long + 2:
        return "neutral"
    sma_short = _mean(prices[-short:])
    sma_long  = _mean(prices[-long:])
    # Inclinação recente: média dos últimos 5 vs últimos 10-15
    slope_recent = _mean(prices[-5:]) - _mean(prices[-15:-5]) if len(prices) >= 15 else 0.0
    mid_price = _mean(prices[-10:]) if len(prices) >= 10 else prices[-1]
    slope_pct = (slope_recent / mid_price) if mid_price > 0 else 0.0

    if sma_short > sma_long * 1.005 and slope_pct > 0.001:
        return "up"
    elif sma_short < sma_long * 0.995 and slope_pct < -0.001:
        return "down"
    return "neutral"


def _calc_atr_ratio(prices: List[float], short_period: int = 5, long_period: int = 20) -> float:
    """
    ATR Ratio = ATR_short / ATR_long.
    > 1.5 → volatilidade alta vs histórico
    < 0.7 → volatilidade baixa (compressão)
    """
    if len(prices) < long_period + 1:
        return 1.0
    atr_short = _atr_simple(prices, short_period)
    atr_long  = _atr_simple(prices, long_period)
    if atr_long == 0:
        return 1.0
    return round(atr_short / atr_long, 3)


# ─────────────────────────────────────────────────────────────
# Capital Multiplier Table
# ─────────────────────────────────────────────────────────────

# Penalidade aplicada quando tendência é "down" — reduz exposição drasticamente
# Evita sangrar em mercados de queda (PF 0.35 no stress test anterior)
DOWNTREND_PENALTY: float = 0.25  # corta 75% de toda exposição em downtrend
DOWNTREND_SAFE_STRATEGIES = {"mr", "vr"}  # MR e VR podem lucrar em queda (reversão)
DOWNTREND_SAFE_PENALTY: float = 0.60  # MR/VR cortam menos (40%)

# (regime, strategy) → multiplier
# estratégias: "5m", "1h", "1d", "mr", "bo", "sq", "ls", "fvg", "vr", "pb"
_REGIME_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    "TREND_STRONG": {
        "5m":  1.05,
        "1h":  1.10,
        "1d":  1.05,
        "mr":  0.30,   # MR falha contra tendências fortes
        "bo":  1.55,   # Breakout excela em tendências
        "sq":  1.20,   # Squeeze pode disparar com tendência
        "ls":  1.15,   # LS ainda válido — grandes players varrem em tendências
        "fvg": 1.10,   # FVGs comuns em movimentos tendenciais
        "vr":  0.20,   # VWAP Reversion péssimo em tendência — desativar quase totalmente
        "pb":  1.60,   # Pyramid Breakout IDEAL em tendência forte — máx boost
    },
    "TREND_WEAK": {
        "5m":  1.00,
        "1h":  1.05,
        "1d":  1.00,
        "mr":  0.65,
        "bo":  1.25,
        "sq":  1.10,
        "ls":  1.05,
        "fvg": 1.05,
        "vr":  0.40,   # VWAP Reversion fraco em tendência fraca
        "pb":  1.35,   # Pyramid Breakout bom em tendência fraca
    },
    "LATERAL": {
        "5m":  0.90,
        "1h":  1.00,
        "1d":  0.85,
        "mr":  1.45,   # MR excela em laterais
        "bo":  0.25,   # Breakout gera falsos sinais em lateral
        "sq":  1.35,   # Squeeze acumula em lateral (pré-expansão)
        "ls":  1.20,   # LS relevante: topo/fundo do range varre stops
        "fvg": 1.25,   # FVG preenchido dentro do range
        "vr":  1.50,   # VWAP Reversion EXCELA em lateral — máx boost
        "pb":  0.20,   # Pyramid Breakout péssimo em lateral — desativar
    },
    "HIGH_VOL": {
        "5m":  0.70,   # ATR alto = posições menores em tudo
        "1h":  0.75,
        "1d":  0.65,
        "mr":  0.55,   # MR perigoso com volatilidade alta
        "bo":  1.30,   # Breakout + volatilidade = movimentos grandes
        "sq":  0.80,
        "ls":  1.40,   # LS muito relevante: volatilidade gera mais stop hunts
        "fvg": 0.85,
        "vr":  0.40,   # VWAP Reversion arriscado com vol alta — stops estourados
        "pb":  1.45,   # Pyramid Breakout bom: vol alta + tendência = lucro
    },
    "LOW_VOL_SQUEEZE": {
        "5m":  0.90,
        "1h":  0.95,
        "1d":  1.00,
        "mr":  1.20,
        "bo":  0.60,   # Sem volatilidade: breakout falha
        "sq":  1.60,   # Squeeze IDEAL: compressão → expansão iminente
        "ls":  0.80,   # Baixa vol = menos stops sendo ativados
        "fvg": 1.10,
        "vr":  1.30,   # VWAP Reversion bom em baixa vol — reversões limpas
        "pb":  0.30,   # Pyramid Breakout fraco sem vol — sem tendência
    },
    "NEUTRAL": {
        "5m":  1.00,
        "1h":  1.00,
        "1d":  1.00,
        "mr":  1.00,
        "bo":  1.00,
        "sq":  1.00,
        "ls":  1.00,
        "fvg": 1.00,
        "vr":  1.00,
        "pb":  1.00,
    },
}


# ─────────────────────────────────────────────────────────────
# Main Regime Detector
# ─────────────────────────────────────────────────────────────

class RegimeDetector:
    """
    Detecta o regime de mercado usando ADX + ATR Ratio + Hurst Exponent.
    Retorna regime classificado + multipliers de capital por estratégia.
    """

    # ADX thresholds
    ADX_STRONG_TREND = 25.0
    ADX_WEAK_TREND   = 20.0
    ADX_LATERAL      = 18.0

    # ATR Ratio thresholds
    ATR_HIGH_VOL     = 1.55
    ATR_LOW_VOL      = 0.65

    # Hurst thresholds
    HURST_TRENDING   = 0.55
    HURST_REVERTING  = 0.45

    @staticmethod
    def detect(prices: List[float], min_periods: int = 42) -> Dict:
        """
        Detecta o regime de mercado atual.

        Args:
            prices: lista de preços de fechamento (ordem cronológica)
            min_periods: mínimo de velas para análise confiável

        Returns:
            Dict com: regime, adx, atr_ratio, hurst,
                      multipliers (dict por estratégia), confidence (0-1),
                      signals (breakdown dos 3 sub-sinais)
        """
        neutral_result = {
            "regime":      "NEUTRAL",
            "direction":   "neutral",
            "adx":         20.0,
            "atr_ratio":   1.0,
            "hurst":       0.5,
            "multipliers": dict(_REGIME_MULTIPLIERS["NEUTRAL"]),
            "confidence":  0.0,
            "signals": {
                "adx_signal":  "neutral",
                "atr_signal":  "normal",
                "hurst_signal": "random",
            },
        }

        if len(prices) < min_periods:
            return neutral_result

        # ── 1. Calcular os 3 indicadores ─────────────────────────────────
        adx       = _calc_adx(prices)
        atr_ratio = _calc_atr_ratio(prices)
        hurst     = _calc_hurst(prices)

        # ── 2. Classificar cada sinal ─────────────────────────────────────
        if adx >= RegimeDetector.ADX_STRONG_TREND:
            adx_signal = "strong_trend"
        elif adx >= RegimeDetector.ADX_WEAK_TREND:
            adx_signal = "weak_trend"
        elif adx <= RegimeDetector.ADX_LATERAL:
            adx_signal = "lateral"
        else:
            adx_signal = "neutral"

        if atr_ratio >= RegimeDetector.ATR_HIGH_VOL:
            atr_signal = "high_vol"
        elif atr_ratio <= RegimeDetector.ATR_LOW_VOL:
            atr_signal = "low_vol_squeeze"
        else:
            atr_signal = "normal"

        if hurst >= RegimeDetector.HURST_TRENDING:
            hurst_signal = "trending"
        elif hurst <= RegimeDetector.HURST_REVERTING:
            hurst_signal = "reverting"
        else:
            hurst_signal = "random"

        # ── 3. Combinar sinais para regime final ──────────────────────────
        # Prioridade: HIGH_VOL > TREND_STRONG > LOW_VOL_SQUEEZE > LATERAL > TREND_WEAK > NEUTRAL

        confidence = 0.0
        regime     = "NEUTRAL"

        if atr_signal == "high_vol":
            regime     = "HIGH_VOL"
            confidence = min(1.0, (atr_ratio - RegimeDetector.ATR_HIGH_VOL) / 0.5 + 0.6)

        elif adx_signal == "strong_trend" and hurst_signal in ("trending", "random"):
            regime     = "TREND_STRONG"
            # Confidence = quão forte é o ADX além do threshold * concordância do Hurst
            adx_conf   = min(1.0, (adx - RegimeDetector.ADX_STRONG_TREND) / 15.0 + 0.6)
            hurst_conf = 0.8 if hurst_signal == "trending" else 0.5
            confidence = round((adx_conf + hurst_conf) / 2, 3)

        elif atr_signal == "low_vol_squeeze":
            regime     = "LOW_VOL_SQUEEZE"
            confidence = min(1.0, (RegimeDetector.ATR_LOW_VOL - atr_ratio) / 0.3 + 0.5)

        elif adx_signal == "lateral" and hurst_signal in ("reverting", "random"):
            regime     = "LATERAL"
            adx_conf   = min(1.0, (RegimeDetector.ADX_LATERAL - adx) / 5.0 + 0.5)
            hurst_conf = 0.8 if hurst_signal == "reverting" else 0.5
            confidence = round((adx_conf + hurst_conf) / 2, 3)

        elif adx_signal == "weak_trend":
            regime     = "TREND_WEAK"
            confidence = min(1.0, (adx - RegimeDetector.ADX_WEAK_TREND) / 8.0 + 0.4)

        else:
            regime     = "NEUTRAL"
            confidence = 0.3

        multipliers = dict(_REGIME_MULTIPLIERS.get(regime, _REGIME_MULTIPLIERS["NEUTRAL"]))

        # ── Trend Direction — detecta up/down e aplica penalidade em downtrend ──
        direction = _detect_direction(prices)
        if direction == "down":
            for strat in multipliers:
                if strat in DOWNTREND_SAFE_STRATEGIES:
                    multipliers[strat] *= DOWNTREND_SAFE_PENALTY
                else:
                    multipliers[strat] *= DOWNTREND_PENALTY

        return {
            "regime":      regime,
            "direction":   direction,
            "adx":         round(adx, 2),
            "atr_ratio":   round(atr_ratio, 3),
            "hurst":       round(hurst, 3),
            "multipliers": multipliers,
            "confidence":  round(confidence, 3),
            "signals": {
                "adx_signal":   adx_signal,
                "atr_signal":   atr_signal,
                "hurst_signal": hurst_signal,
            },
        }

    @staticmethod
    def apply_multipliers(base_capitals: Dict[str, float], regime_result: Dict) -> Dict[str, float]:
        """
        Aplica os multipliers de regime nos capitais base.

        Args:
            base_capitals: {"mr": 100.0, "bo": 80.0, ...}
            regime_result: saída de RegimeDetector.detect()

        Returns:
            Dict com capitais ajustados pelo regime.
        """
        multipliers = regime_result.get("multipliers", {})
        return {
            k: round(v * multipliers.get(k, 1.0), 2)
            for k, v in base_capitals.items()
        }
