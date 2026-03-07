"""
Liquidity Sweep Engine v1  (Stop Hunt / Caça de Liquidez)

Detecta rompimentos falsos onde grandes players varrem liquidez (stops) antes de reverter.

Fenômeno: o preço rompe uma máxima/mínima significativa, ativa ordens stop de traders,
depois reverte rapidamente. Instituições usam esse movimento para construir posições.

Lógica:
  LONG (sweep de fundo):
    - Preço rompe a mínima dos últimos N candles (sweep de stops de compra)
    - Fecha de volta acima da mínima no mesmo ou próximo candle
    → Sinal de LONG: fundo varrido, stops tomados, reversão provável

  SHORT (sweep de topo):
    - Preço rompe a máxima dos últimos N candles (sweep de stops de venda)
    - Fecha de volta abaixo da máxima no mesmo ou próximo candle
    → Sinal de SHORT: topo varrido, stops tomados, reversão provável

Diferença do Breakout: aqui queremos o RETORNO após o rompimento, não a continuidade.
Correlação muito baixa (ou negativa) com Breakout engine.

Edge estrutural: muito forte em cripto e forex onde stops são concentrados em níveis visíveis.

Score 0-1; entry_valid quando score >= 0.60
"""

from typing import Dict, List


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _roc(prices: List[float], period: int = 3) -> float:
    if len(prices) < period + 1:
        return 0.0
    base = prices[-(period + 1)]
    return (prices[-1] - base) / base if base != 0 else 0.0


def _atr_simple(prices: List[float], period: int = 10) -> float:
    if len(prices) < 2:
        return 0.0
    trs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    recent = trs[-period:] if len(trs) >= period else trs
    return _mean(recent)


class LiquiditySweepAnalyzer:
    """Engine de Liquidity Sweep v1 — detecta stop hunts e reversões pós-sweep."""

    # Score mínimo para entry_valid
    LS_THRESHOLD = 0.60

    # Lookback para calcular a zona de liquidez (onde estão os stops)
    LOOKBACK = 20

    # Rompimento mínimo (%), abaixo disso não é um sweep relevante
    MIN_SWEEP_PCT = 0.003   # 0.3%

    # Reversão mínima (%) para confirmar que o preço voltou para dentro do range
    MIN_REVERSAL_PCT = 0.002  # 0.2%

    @staticmethod
    def calculate_sweep_score(prices: List[float], volumes: List[float]) -> Dict:
        """
        Calcula o score de liquidity sweep para um ativo.

        Returns:
            Dict com sweep_score (0-1), entry_valid, direction (LONG/SHORT/NONE),
            swept_level, reversal_pct, sweep_depth_pct e sub-scores.
        """
        MIN_PERIODS = LiquiditySweepAnalyzer.LOOKBACK + 3
        empty = {
            "sweep_score": 0.0, "entry_valid": False, "valid": False,
            "direction": "NONE", "swept_level": 0.0,
            "sweep_depth_pct": 0.0, "reversal_pct": 0.0,
            "current_price": prices[-1] if prices else 0.0,
        }
        if len(prices) < MIN_PERIODS:
            return empty

        current_price = prices[-1]
        prev_price    = prices[-2]   # candle que "varrreu" a liquidez
        if current_price <= 0 or prev_price <= 0:
            return empty

        # ── 1. Zona de liquidez: máxima e mínima dos últimos N candles (excluindo os 2 últimos) ─
        zone_prices  = prices[-(LiquiditySweepAnalyzer.LOOKBACK + 2):-2]
        if not zone_prices:
            return empty

        resistance   = max(zone_prices)   # onde os stops de SHORT estão acima
        support      = min(zone_prices)   # onde os stops de LONG estão abaixo

        if resistance <= 0 or support <= 0:
            return empty

        # ── 2. Detectar se houve sweep (candle anterior rompeu o nível) ─────────
        # SHORT sweep (topo): candle anterior ficou ACIMA da resistência, atual voltou
        short_swept = prev_price > resistance
        short_reversal_pct = (prev_price - current_price) / prev_price if short_swept else 0.0
        short_sweep_depth = (prev_price - resistance) / resistance if short_swept else 0.0

        # LONG sweep (fundo): candle anterior ficou ABAIXO do suporte, atual voltou
        long_swept = prev_price < support
        long_reversal_pct = (current_price - prev_price) / prev_price if long_swept else 0.0
        long_sweep_depth = (support - prev_price) / support if long_swept else 0.0

        direction = "NONE"
        swept_level = 0.0
        sweep_depth_pct = 0.0
        reversal_pct = 0.0

        if short_swept and short_reversal_pct >= LiquiditySweepAnalyzer.MIN_REVERSAL_PCT:
            # Topo varrido e preço voltou → SHORT
            if current_price < resistance:  # voltou para dentro do range
                direction       = "SHORT"
                swept_level     = resistance
                sweep_depth_pct = short_sweep_depth
                reversal_pct    = short_reversal_pct
        elif long_swept and long_reversal_pct >= LiquiditySweepAnalyzer.MIN_REVERSAL_PCT:
            # Fundo varrido e preço voltou → LONG
            if current_price > support:  # voltou para dentro do range
                direction       = "LONG"
                swept_level     = support
                sweep_depth_pct = long_sweep_depth
                reversal_pct    = long_reversal_pct

        if direction == "NONE":
            return {**empty, "valid": True, "current_price": float(current_price),
                    "resistance": float(resistance), "support": float(support)}

        # Verificar que o sweep mínimo ocorreu
        if sweep_depth_pct < LiquiditySweepAnalyzer.MIN_SWEEP_PCT:
            return {**empty, "valid": True, "current_price": float(current_price)}

        # ── 3. Score da profundidade do sweep ────────────────────────────────
        # Quanto mais profundo o wick que varrreu, mais stops foram tomados
        # 0.3% = 0.3, 1.0% = 0.8, 2%+ = 1.0
        depth_score = min(1.0, sweep_depth_pct / 0.015)

        # ── 4. Score da velocidade de reversão ───────────────────────────────
        # Reversão rápida = mais credível (preço não aceitou o nível rompido)
        reversal_score = min(1.0, reversal_pct / 0.01)

        # ── 5. Volume do candle de sweep (alto volume = mais stops ativados) ─
        vol_score = 0.0
        if len(volumes) >= 3 and volumes:
            avg_vol  = _mean(volumes[-LiquiditySweepAnalyzer.LOOKBACK:-1]) if len(volumes) > LiquiditySweepAnalyzer.LOOKBACK else _mean(volumes[:-1])
            sweep_vol = volumes[-2]  # volume do candle que varrreu
            if avg_vol > 0:
                vol_ratio = sweep_vol / avg_vol
                vol_score = min(1.0, max(0.0, (vol_ratio - 1.0) / 2.0))

        # ── 6. Confirmação de momentum de reversão (ROC confirma) ────────────
        roc_now = _roc(prices, period=1)  # ROC de 1 candle
        momentum_score = 0.0
        if direction == "SHORT" and roc_now < -0.001:
            momentum_score = min(1.0, abs(roc_now) / 0.01)
        elif direction == "LONG" and roc_now > 0.001:
            momentum_score = min(1.0, roc_now / 0.01)

        # ── 7. Score final ponderado ──────────────────────────────────────────
        # Depth + reversal são os ingredientes principais
        # Volume confirma que stops reais foram ativados
        # Momentum valida que a reversão está em curso
        raw_score = (
            0.35 * depth_score        # profundidade do sweep
            + 0.30 * reversal_score   # velocidade da reversão
            + 0.20 * vol_score        # volume no sweep
            + 0.15 * momentum_score   # confirmação de reversão
        )

        sweep_score = round(raw_score, 4)
        entry_valid = sweep_score >= LiquiditySweepAnalyzer.LS_THRESHOLD

        return {
            "sweep_score":      sweep_score,
            "entry_valid":      entry_valid,
            "valid":            True,
            "direction":        direction,
            "swept_level":      float(swept_level),
            "sweep_depth_pct":  float(sweep_depth_pct),
            "reversal_pct":     float(reversal_pct),
            "depth_score":      float(depth_score),
            "reversal_score":   float(reversal_score),
            "vol_score":        float(vol_score),
            "momentum_score":   float(momentum_score),
            "current_price":    float(current_price),
        }

    @staticmethod
    def calculate_multiple_assets(
        assets_data: Dict[str, Dict],
        top_n: int = 1,
    ) -> Dict[str, Dict]:
        """
        Analisa todos os ativos e retorna os top_n candidatos para liquidity sweep.

        Args:
            assets_data: {asset: {prices: [...], volumes: [...]}}
            top_n: máx de candidatos retornados (padrão 1)

        Returns:
            Dict {asset: resultado} ordenado por sweep_score desc.
        """
        results = {}
        for asset, data in assets_data.items():
            prices  = data.get("prices", [])
            volumes = data.get("volumes", [])
            result  = LiquiditySweepAnalyzer.calculate_sweep_score(prices, volumes)
            if result["entry_valid"]:
                results[asset] = result

        sorted_results = sorted(
            results.items(),
            key=lambda x: x[1]["sweep_score"],
            reverse=True
        )
        return dict(sorted_results[:top_n])
