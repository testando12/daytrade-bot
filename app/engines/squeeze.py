"""
Volatility Compression → Expansion Engine v1  (Bollinger Squeeze)

Detecta compressão de volatilidade seguida de expansão direcional.
Fenômeno: mercados alternam entre consolidação (baixa vol) e tendência (alta vol).
Após compressão forte → a expansão tende a ser rápida e direcional.

Indicadores:
  1. Bollinger Band Width percentil histórico (< P15 = comprimido)
  2. Keltner Channel interno (BB dentro do KC = squeeze confirmado — LazyBear method)
  3. Direção do breakout pós-squeeze (rompe BB superior = LONG, inferior = SHORT)
  4. Momentum pós-squeeze (ROC curto confirma direção)
  5. Duração do squeeze (mais ciclos comprimido = expansão mais forte)

Win rate esperado: 35–45%  |  R/R esperado: 2.5–4.0
Correlação baixa com Momentum e MR = diversificação real de edge.

Threshold: score >= 0.58 para entry_valid = True
"""

from typing import Dict, List


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stddev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return (sum((v - m) ** 2 for v in values) / len(values)) ** 0.5


def _atr_simple(prices: List[float], period: int = 14) -> float:
    """ATR simplificado usando apenas fechamentos."""
    if len(prices) < 2:
        return 0.0
    trs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    recent = trs[-period:] if len(trs) >= period else trs
    return _mean(recent)


def _roc(prices: List[float], period: int = 5) -> float:
    if len(prices) < period + 1:
        return 0.0
    base = prices[-(period + 1)]
    return (prices[-1] - base) / base if base != 0 else 0.0


def _ema_simple(prices: List[float], period: int) -> float:
    """EMA simples para filtro de tendência."""
    if not prices or len(prices) < period:
        return prices[-1] if prices else 0.0
    mult = 2.0 / (period + 1.0)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = (p - ema) * mult + ema
    return ema


def _bb_width_series(prices: List[float], period: int = 20, std_dev: float = 2.0) -> List[float]:
    """Calcula a série de larguras das Bollinger Bands para detectar compressão histórica."""
    widths = []
    for i in range(period, len(prices) + 1):
        window = prices[i - period:i]
        m = _mean(window)
        s = _stddev(window)
        if m > 0:
            widths.append((std_dev * 2 * s) / m)  # BB width relativa à média
        else:
            widths.append(0.0)
    return widths


class SqueezeAnalyzer:
    """Engine de Volatility Compression v1 — detecta Bollinger Squeeze com expansão direcional."""

    # Score mínimo para entry_valid
    SQ_THRESHOLD = 0.58

    # Bollinger Bands
    BB_PERIOD  = 20
    BB_STD_DEV = 2.0

    # Keltner Channel
    KC_PERIOD  = 20
    KC_MULT    = 1.5   # KC usa ATR×1.5 (LazyBear padrão)

    # Percentil de BB width que define "comprimido"
    COMPRESSION_PERCENTILE = 15  # largura < P15 histórico = squeeze

    # Candles mínimos em squeeze para contar como squeeze real
    MIN_SQUEEZE_BARS = 3

    @staticmethod
    def calculate_squeeze_score(prices: List[float], volumes: List[float]) -> Dict:
        """
        Calcula o score de squeeze para um ativo.

        Returns:
            Dict com squeeze_score (0-1), entry_valid, direction (LONG/SHORT/NONE),
            in_squeeze, squeeze_bars, bb_width_pct, kc_squeeze, momentum_dir e sub-scores.
        """
        MIN_PERIODS = SqueezeAnalyzer.BB_PERIOD + SqueezeAnalyzer.MIN_SQUEEZE_BARS + 5
        empty = {
            "squeeze_score": 0.0, "entry_valid": False, "valid": False,
            "direction": "NONE", "in_squeeze": False, "squeeze_bars": 0,
            "bb_width_pct": 50.0, "kc_squeeze": False, "momentum_dir": 0.0,
            "compression_score": 0.0, "duration_score": 0.0, "momentum_score_raw": 0.0,
            "current_price": prices[-1] if prices else 0.0,
        }
        if len(prices) < MIN_PERIODS:
            return empty

        current_price = prices[-1]
        if current_price <= 0:
            return empty

        # ── 1. Bollinger Bands atual ──────────────────────────────────────────
        bb_window = prices[-SqueezeAnalyzer.BB_PERIOD:]
        bb_mean   = _mean(bb_window)
        bb_std    = _stddev(bb_window)
        bb_upper  = bb_mean + SqueezeAnalyzer.BB_STD_DEV * bb_std
        bb_lower  = bb_mean - SqueezeAnalyzer.BB_STD_DEV * bb_std

        if bb_mean <= 0:
            return empty

        # BB width relativa (normalizada pela média)
        bb_width_current = (bb_upper - bb_lower) / bb_mean

        # ── 2. Histórico de BB width para calcular percentil ─────────────────
        # Usa os últimos 100 candles para estimar distribuição histórica
        hist_lookback = min(len(prices), 100)
        hist_prices = prices[-hist_lookback:]
        width_series = _bb_width_series(hist_prices, SqueezeAnalyzer.BB_PERIOD, SqueezeAnalyzer.BB_STD_DEV)

        if not width_series:
            return empty

        sorted_widths = sorted(width_series)
        pct_idx = int(len(sorted_widths) * SqueezeAnalyzer.COMPRESSION_PERCENTILE / 100)
        pct_threshold = sorted_widths[max(0, pct_idx)]

        # Percentil atual (0 = extremamente comprimido, 100 = extremamente expandido)
        ranks_below = sum(1 for w in width_series if w <= bb_width_current)
        bb_width_pct = round(100.0 * ranks_below / len(width_series), 1)

        in_compression = bb_width_current <= pct_threshold

        # ── 3. Keltner Channel (confirma squeeze — LazyBear method) ──────────
        atr_val = _atr_simple(prices[-SqueezeAnalyzer.KC_PERIOD - 5:], SqueezeAnalyzer.KC_PERIOD)
        kc_upper = bb_mean + SqueezeAnalyzer.KC_MULT * atr_val
        kc_lower = bb_mean - SqueezeAnalyzer.KC_MULT * atr_val

        # Squeeze verdadeiro: BB completamente dentro do Keltner Channel
        kc_squeeze = (bb_upper <= kc_upper) and (bb_lower >= kc_lower)

        # ── 4. Contar quantos candles consecutivos em squeeze ─────────────────
        squeeze_bars = 0
        n = min(len(prices), 50)
        for i in range(n, 0, -1):
            sub = prices[-i:]
            if len(sub) < SqueezeAnalyzer.BB_PERIOD:
                break
            sub_win = sub[-SqueezeAnalyzer.BB_PERIOD:]
            sub_m   = _mean(sub_win)
            sub_s   = _stddev(sub_win)
            if sub_m <= 0:
                break
            sub_w = (2 * SqueezeAnalyzer.BB_STD_DEV * sub_s) / sub_m
            if sub_w <= pct_threshold * 1.1:  # 10% tolerância
                squeeze_bars += 1
            else:
                break

        in_squeeze = in_compression or kc_squeeze

        # ── 5. Detectar direção do breakout pós-squeeze ───────────────────────
        # Só entra se há expansão: preço saiu das bandas após compressão
        direction = "NONE"
        breakout_pct = 0.0

        long_break  = (current_price - bb_upper) / bb_mean if bb_mean > 0 else 0.0
        short_break = (bb_lower - current_price) / bb_mean if bb_mean > 0 else 0.0

        # Precisa ter estado em squeeze nos ciclos recentes para o sinal ser válido
        recently_squeezed = squeeze_bars >= SqueezeAnalyzer.MIN_SQUEEZE_BARS or bb_width_pct <= 25

        if recently_squeezed:
            if current_price > bb_upper and long_break > 0:
                direction    = "LONG"
                breakout_pct = long_break
            elif current_price < bb_lower and short_break > 0:
                direction    = "SHORT"
                breakout_pct = short_break

        if direction == "NONE":
            return {**empty, "valid": True, "in_squeeze": in_squeeze,
                    "squeeze_bars": int(squeeze_bars), "bb_width_pct": float(bb_width_pct),
                    "kc_squeeze": kc_squeeze, "current_price": float(current_price)}

        # ── 6. Score de compressão ────────────────────────────────────────────
        # bb_width_pct baixo = muito comprimido = score alto
        # P0 = 1.0, P15 = 0.5, P30 = 0.0
        if bb_width_pct <= 5:
            compression_score = 1.0
        elif bb_width_pct <= 15:
            compression_score = 0.5 + (15 - bb_width_pct) / 20.0
        elif bb_width_pct <= 30:
            compression_score = max(0.0, (30 - bb_width_pct) / 30.0)
        else:
            compression_score = 0.0

        # Boost se KC confirma squeeze
        if kc_squeeze:
            compression_score = min(1.0, compression_score * 1.3)

        # ── 7. Score de duração do squeeze ────────────────────────────────────
        # Mais candles em squeeze = expansão mais energética esperada
        # 3 bars = 0.3, 10 bars = 0.7, 20+ bars = 1.0
        duration_score = min(1.0, squeeze_bars / 20.0)

        # ── 8. Momentum confirmando direção ──────────────────────────────────
        roc_short = _roc(prices, period=3)
        roc_med   = _roc(prices, period=7)

        momentum_score_raw = 0.0
        if direction == "LONG":
            if roc_short > 0.001:
                momentum_score_raw = min(1.0, roc_short / 0.02)
            elif roc_short > 0:
                momentum_score_raw = 0.25
        elif direction == "SHORT":
            if roc_short < -0.001:
                momentum_score_raw = min(1.0, abs(roc_short) / 0.02)
            elif roc_short < 0:
                momentum_score_raw = 0.25

        # Boost se ROC médio concorda
        if direction == "LONG" and roc_med > 0:
            momentum_score_raw = min(1.0, momentum_score_raw * 1.15)
        elif direction == "SHORT" and roc_med < 0:
            momentum_score_raw = min(1.0, momentum_score_raw * 1.15)

        # ── 9. Score final ponderado ──────────────────────────────────────────
        # Compressão é o ingrediente principal (sem squeeze, não é a estratégia)
        # Duração amplifica a expectativa de expansão
        # Momentum valida que o movimento realmente começou
        raw_score = (
            0.45 * compression_score    # qualidade da compressão
            + 0.25 * duration_score     # duração da acumulação
            + 0.30 * momentum_score_raw  # confirmação de direção
        )

        # Penalidade se squeeze inferior ao mínimo
        if not recently_squeezed:
            raw_score *= 0.30

        # ── v2: filtro de tendência EMA20 vs EMA50 ───────────────────────────
        # Squeeze breakouts contra a tendência principal falham muito mais
        # Penaliza fortemente entradas contra EMA50 para melhorar WR
        ema20 = _ema_simple(prices[-80:], 20) if len(prices) >= 20 else current_price
        ema50 = _ema_simple(prices[-100:], 50) if len(prices) >= 50 else current_price
        trend_up = ema20 >= ema50
        trend_aligned = (
            (direction == "LONG" and trend_up) or
            (direction == "SHORT" and not trend_up)
        )
        if trend_aligned:
            raw_score = min(1.0, raw_score * 1.10)  # boost leve quando alinhado
        else:
            raw_score *= 0.68  # penalidade — breakout contra tendência tem WR baixo

        squeeze_score = round(raw_score, 4)
        entry_valid   = squeeze_score >= SqueezeAnalyzer.SQ_THRESHOLD

        return {
            "squeeze_score":      squeeze_score,
            "entry_valid":        entry_valid,
            "valid":              True,
            "direction":          direction,
            "in_squeeze":         in_squeeze,
            "squeeze_bars":       int(squeeze_bars),
            "bb_width_pct":       float(bb_width_pct),
            "kc_squeeze":         kc_squeeze,
            "breakout_pct":       float(breakout_pct),
            "compression_score":  float(compression_score),
            "duration_score":     float(duration_score),
            "momentum_score_raw": float(momentum_score_raw),
            "current_price":      float(current_price),
        }

    @staticmethod
    def calculate_multiple_assets(
        assets_data: Dict[str, Dict],
        top_n: int = 1,
    ) -> Dict[str, Dict]:
        """
        Analisa todos os ativos e retorna os top_n candidatos para squeeze.

        Args:
            assets_data: {asset: {prices: [...], volumes: [...]}}
            top_n: máx de candidatos retornados (padrão 1)

        Returns:
            Dict {asset: resultado} dos candidatos válidos, ordenados por squeeze_score desc.
        """
        results = {}
        for asset, data in assets_data.items():
            prices  = data.get("prices", [])
            volumes = data.get("volumes", [])
            result  = SqueezeAnalyzer.calculate_squeeze_score(prices, volumes)
            if result["entry_valid"]:
                results[asset] = result

        sorted_results = sorted(
            results.items(),
            key=lambda x: x[1]["squeeze_score"],
            reverse=True
        )
        return dict(sorted_results[:top_n])
