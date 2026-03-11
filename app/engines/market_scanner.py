"""
Market Scanner — Fase 1 do pipeline de análise

Triagem rápida de 170+ ativos para selecionar os top 15-20 mais interessantes
do momento. Evita calcular momentum completo para ativos "dormindo".

Critérios:
  1. Volume spike: volume recente > 1.5x média histórica
  2. Price move: variação abs recente > 0.3% (algo está acontecendo)
  3. Scanner score = volume_ratio * abs_move_pct  →  rankeia por relevância

Uso:
    candidates, scan_results = MarketScanner.scan(assets_data, top_n=20)
    momentum_results = MomentumAnalyzer.calculate_multiple_assets(
        {a: assets_data[a] for a in candidates}
    )
"""

from typing import Dict, List, Tuple

# ── Parâmetros ────────────────────────────────────────────────────────────────
_DEFAULT_TOP_N    = 20    # candidatos que passam para análise profunda
_MIN_VOLUME_RATIO = 1.5   # volume recente >= 1.5x média → ativo "acordado"
_MIN_MOVE_PCT     = 0.003 # mínimo 0.3% de variação → algo está acontecendo
_LOOKBACK         = 5     # últimos N candles para calcular retorno/volume recente
_VOLUME_WINDOW    = 20    # janela para volume médio histórico


def _mean(lst: List[float]) -> float:
    return sum(lst) / len(lst) if lst else 0.0


class MarketScanner:
    """
    Scanner rápido de mercado — Fase 1 do ciclo.

    170 ativos → top 20 candidatos em ~1-2s, sem cálculo pesado de indicadores.
    """

    TOP_N         = _DEFAULT_TOP_N
    MIN_VOL_RATIO = _MIN_VOLUME_RATIO
    MIN_MOVE_PCT  = _MIN_MOVE_PCT

    @staticmethod
    def score_asset(prices: List[float], volumes: List[float]) -> Dict:
        """
        Score rápido de um único ativo.

        Returns:
            Dict com: scanner_score, volume_ratio, move_pct, direction, passed
        """
        if len(prices) < _LOOKBACK + 2:
            return {
                "scanner_score": 0.0,
                "volume_ratio":  0.0,
                "move_pct":      0.0,
                "direction":     "flat",
                "passed":        False,
            }

        # Volume ratio: média dos últimos _LOOKBACK vs janela histórica
        recent_vol = _mean(volumes[-_LOOKBACK:])  if len(volumes) >= _LOOKBACK  else 0.0
        hist_vol   = _mean(volumes[-_VOLUME_WINDOW:]) if len(volumes) >= _VOLUME_WINDOW else recent_vol
        vol_ratio  = (recent_vol / hist_vol) if hist_vol > 0 else 1.0

        # Retorno absoluto nos últimos _LOOKBACK candles
        price_now  = prices[-1]
        price_back = prices[-_LOOKBACK - 1]
        raw_move   = (price_now - price_back) / price_back if price_back > 0 else 0.0
        move_pct   = abs(raw_move)

        # Direção (útil para o ciclo saber se é bull/bear momentum)
        direction  = "up" if raw_move > 0.001 else ("down" if raw_move < -0.001 else "flat")

        # Score final: produto dos dois componentes
        score  = vol_ratio * move_pct

        # Ativo "passa" se volume ou movimento justificam análise
        passed = (vol_ratio >= _MIN_VOLUME_RATIO) or (move_pct >= _MIN_MOVE_PCT)

        return {
            "scanner_score": round(score, 6),
            "volume_ratio":  round(vol_ratio, 3),
            "move_pct":      round(move_pct * 100, 3),  # em %
            "direction":     direction,
            "passed":        passed,
        }

    @staticmethod
    def scan(
        assets_data: Dict[str, Dict],
        top_n: int = _DEFAULT_TOP_N,
        force_include: List[str] = None,
    ) -> Tuple[List[str], Dict[str, Dict]]:
        """
        Escaneia todos os ativos e retorna os top_n mais interessantes.

        Args:
            assets_data:   {asset: {"prices": [...], "volumes": [...]}}
            top_n:         quantos candidatos a retornar
            force_include: ativos que sempre entram (ex: BTC como referência para IRQ)

        Returns:
            (candidates: List[str], scan_results: Dict[str, Dict])
        """
        force_include = force_include or []
        scores: Dict[str, Dict] = {}

        for asset, data in assets_data.items():
            prices  = data.get("prices", [])
            volumes = data.get("volumes", [])
            result  = MarketScanner.score_asset(prices, volumes)
            scores[asset] = {**result, "asset": asset}

        # Ordena por scanner_score decrescente
        ranked = sorted(
            scores.items(),
            key=lambda x: x[1]["scanner_score"],
            reverse=True,
        )

        # Candidatos que passaram no filtro mínimo, limitados a top_n
        candidates: List[str] = [a for a, s in ranked if s["passed"]][:top_n]

        # Adiciona forçados sem duplicar
        for asset in force_include:
            if asset in scores and asset not in candidates:
                candidates.append(asset)

        # Se mercado está lento (poucos passaram), completa com os melhores rankeados
        if len(candidates) < top_n:
            for asset, _ in ranked:
                if asset not in candidates:
                    candidates.append(asset)
                if len(candidates) >= top_n:
                    break

        # Adiciona rank de scan no resultado
        for rank, (asset, _) in enumerate(ranked, 1):
            scores[asset]["scan_rank"] = rank

        return candidates, scores

    @staticmethod
    def summary(scan_results: Dict[str, Dict], candidates: List[str]) -> Dict:
        """Retorna um resumo legível do scan para logging."""
        total     = len(scan_results)
        passed    = sum(1 for s in scan_results.values() if s["passed"])
        top5      = candidates[:5]
        up_count  = sum(1 for a in candidates if scan_results.get(a, {}).get("direction") == "up")
        down_count= sum(1 for a in candidates if scan_results.get(a, {}).get("direction") == "down")
        return {
            "total_assets":    total,
            "passed_filter":   passed,
            "selected":        len(candidates),
            "top5":            top5,
            "up":              up_count,
            "down":            down_count,
        }
