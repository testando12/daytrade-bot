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
    # v2: reduzido 20→10 — níveis mais próximos, mais cruzamentos detectados
    LOOKBACK = 10

    # Rompimento mínimo (%), abaixo disso não é um sweep relevante
    # v2: reduzido 0.3%→0.1% — com dados de fechamento sweeps são menores
    MIN_SWEEP_PCT = 0.001   # 0.1%

    # Reversão mínima (%) para confirmar que o preço voltou para dentro do range
    # v2: reduzido 0.2%→0.05%
    MIN_REVERSAL_PCT = 0.0005  # 0.05%

    # Janela de bars para procurar o sweep (verifica -2, -3, -4)
    SWEEP_WINDOW = 4

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
        if current_price <= 0:
            return empty

        # ── 1. Zona de liquidez: máxima e mínima excluindo a janela de sweep ──────
        # v2: SWEEP_WINDOW+1 bars excluídos para que o sweep possa estar nessa janela
        win = LiquiditySweepAnalyzer.SWEEP_WINDOW + 1
        zone_prices = prices[-(LiquiditySweepAnalyzer.LOOKBACK + win):-win]
        if not zone_prices:
            return empty

        resistance = max(zone_prices)   # onde os stops de SHORT estão acima
        support    = min(zone_prices)   # onde os stops de LONG estão abaixo

        if resistance <= 0 or support <= 0:
            return empty

        # ── 2. v2: Detectar sweep em janela de SWEEP_WINDOW bars ───────────────────
        # Verifica se qualquer bar recente cruzou um nível e o atual reverteu
        # Com dados de fechamento, sweeps são menores — janela maior compensa
        direction       = "NONE"
        swept_level     = 0.0
        sweep_depth_pct = 0.0
        reversal_pct    = 0.0

        for lag in range(2, LiquiditySweepAnalyzer.SWEEP_WINDOW + 2):
            if lag >= len(prices):
                break
            sweep_price = prices[-lag]
            if sweep_price <= 0:
                continue

            # SHORT sweep: bar passado fechou acima da resistência, atual voltou abaixo
            if sweep_price > resistance:
                rev   = (sweep_price - current_price) / sweep_price
                depth = (sweep_price - resistance) / resistance
                if (rev >= LiquiditySweepAnalyzer.MIN_REVERSAL_PCT
                        and current_price < resistance
                        and depth > sweep_depth_pct):
                    direction       = "SHORT"
                    swept_level     = resistance
                    sweep_depth_pct = depth
                    reversal_pct    = rev

            # LONG sweep: bar passado fechou abaixo do suporte, atual voltou acima
            elif sweep_price < support:
                rev   = (current_price - sweep_price) / sweep_price
                depth = (support - sweep_price) / support
                if (rev >= LiquiditySweepAnalyzer.MIN_REVERSAL_PCT
                        and current_price > support
                        and depth > sweep_depth_pct
                        and direction == "NONE"):
                    direction       = "LONG"
                    swept_level     = support
                    sweep_depth_pct = depth
                    reversal_pct    = rev

        if direction == "NONE":
            return {**empty, "valid": True, "current_price": float(current_price),
                    "resistance": float(resistance), "support": float(support)}

        # Verificar que o sweep mínimo ocorreu
        if sweep_depth_pct < LiquiditySweepAnalyzer.MIN_SWEEP_PCT:
            return {**empty, "valid": True, "current_price": float(current_price)}

        # ── 3. Score da profundidade do sweep ────────────────────────────────
        # v2: ajustado para profundidades menores (dados de fechamento)
        # 0.1% = 0.3, 0.5% = 0.8, 1%+ = 1.0
        depth_score = min(1.0, sweep_depth_pct / 0.005)

        # ── 4. Score da velocidade de reversão ───────────────────────────────
        # Reversão rápida = mais credível (preço não aceitou o nível rompido)
        # v2: calibrado para movimentos menores de fechamento
        reversal_score = min(1.0, reversal_pct / 0.005)

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
        entry_valid = sweep_score >= 0.52  # v2: reduzido 0.60→0.52

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
