"""
LAB — Laboratório de Estratégias (Railway)
Recebe dados de mercado do bot local, roda TODOS os 9 engines em simulação,
e armazena resultados para análise comparativa.
"""

from datetime import datetime
from typing import Dict, List, Optional
import traceback

from app.engines import MomentumAnalyzer, RiskAnalyzer
from app.engines.mean_reversion import MeanReversionAnalyzer
from app.engines.breakout import BreakoutAnalyzer
from app.engines.squeeze import SqueezeAnalyzer
from app.engines.liquidity_sweep import LiquiditySweepAnalyzer
from app.engines.fvg import FVGAnalyzer
from app.engines.regime import RegimeDetector
from app.engines.vwap_reversion import VWAPReversionAnalyzer
from app.engines.pyramid_breakout import PyramidBreakoutAnalyzer

# ── Estado do Lab ──────────────────────────────────────────────────────
_lab_state: Dict = {
    "capital_virtual": 500.0,         # capital simulado (mesmo valor do real)
    "total_cycles": 0,
    "last_feed_at": None,
    "engines": {},                     # resultados por engine
    "history": [],                     # últimos N ciclos
}

_MAX_HISTORY = 200  # guarda últimos 200 ciclos

# Engine configs: nome → (classe, alloc%)
_ENGINES = {
    "momentum":          {"class": None,                    "alloc": 0.34},
    "mean_reversion":    {"class": MeanReversionAnalyzer,   "alloc": 0.15},
    "breakout":          {"class": BreakoutAnalyzer,        "alloc": 0.15},
    "squeeze":           {"class": SqueezeAnalyzer,         "alloc": 0.08},
    "liquidity_sweep":   {"class": LiquiditySweepAnalyzer,  "alloc": 0.08},
    "fvg":               {"class": FVGAnalyzer,             "alloc": 0.05},
    "vwap_reversion":    {"class": VWAPReversionAnalyzer,   "alloc": 0.08},
    "pyramid_breakout":  {"class": PyramidBreakoutAnalyzer, "alloc": 0.07},
}


def _init_engine_state(name: str) -> dict:
    return {
        "name": name,
        "total_pnl": 0.0,
        "win_count": 0,
        "loss_count": 0,
        "total_gain": 0.0,
        "total_loss": 0.0,
        "last_signal": None,
        "last_pnl": 0.0,
        "signals_history": [],
    }


def _calc_simple_pnl(klines: dict, asset: str, signal: str, capital: float) -> float:
    """Calcula P&L simples baseado no movimento do último candle."""
    data = klines.get(asset, {})
    prices = data.get("prices", [])
    if len(prices) < 2:
        return 0.0

    ret = (prices[-1] - prices[-2]) / prices[-2]

    # Aplica sinal
    if signal == "SELL":
        ret = -ret
    elif signal != "BUY":
        return 0.0

    # Custos simulados (0.2% round-trip)
    cost = capital * 0.002
    pnl = round(capital * ret - cost, 2)
    return pnl


def run_lab_cycle(klines_1h: dict, klines_5m: Optional[dict] = None,
                  klines_1d: Optional[dict] = None) -> dict:
    """
    Roda todos os engines com os dados recebidos.
    Retorna resultado comparativo de cada engine.
    """
    capital = _lab_state["capital_virtual"]
    results = {}

    # ── 1. Regime Detection ───────────────────────────────────────────
    regime_info = {"regime": "UNKNOWN"}
    try:
        first_asset = next(iter(klines_1h), None)
        if first_asset:
            prices = klines_1h[first_asset].get("prices", [])
            if len(prices) >= 20:
                regime_info = RegimeDetector.detect(prices)
    except Exception:
        pass

    # ── 2. Momentum (usa 1h) ─────────────────────────────────────────
    try:
        mom_results = MomentumAnalyzer.calculate_multiple_assets(klines_1h)
        engine_name = "momentum"
        if engine_name not in _lab_state["engines"]:
            _lab_state["engines"][engine_name] = _init_engine_state(engine_name)

        eng = _lab_state["engines"][engine_name]
        eng_capital = round(capital * _ENGINES[engine_name]["alloc"], 2)

        # Melhor ativo por momentum
        best = max(mom_results.items(),
                   key=lambda x: x[1].get("momentum_score", 0),
                   default=(None, {}))

        signal = "HOLD"
        pnl = 0.0
        if best[0] and best[1].get("momentum_score", 0) >= 0.55:
            signal = best[1].get("signal", "HOLD")
            pnl = _calc_simple_pnl(klines_1h, best[0], signal, eng_capital)

        eng["last_signal"] = {"asset": best[0], "signal": signal,
                              "score": round(best[1].get("momentum_score", 0), 3)}
        eng["last_pnl"] = pnl
        _update_engine_stats(eng, pnl)

        results[engine_name] = {
            "signal": signal, "asset": best[0],
            "score": round(best[1].get("momentum_score", 0), 3),
            "pnl": pnl, "capital": eng_capital,
        }
    except Exception as e:
        results["momentum"] = {"error": str(e)}

    # ── 3. Engines dedicados (MR, BO, SQ, LS, FVG, VR, PB) ──────────
    for engine_name, cfg in _ENGINES.items():
        if engine_name == "momentum":
            continue  # já processado acima
        if cfg["class"] is None:
            continue

        if engine_name not in _lab_state["engines"]:
            _lab_state["engines"][engine_name] = _init_engine_state(engine_name)

        eng = _lab_state["engines"][engine_name]
        eng_capital = round(capital * cfg["alloc"], 2)

        try:
            analyzer = cfg["class"]
            engine_results = analyzer.calculate_multiple_assets(klines_1h, top_n=2)

            signal = "HOLD"
            pnl = 0.0
            best_asset = None

            for asset, data in engine_results.items():
                sig = data.get("signal", "HOLD")
                if sig in ("BUY", "SELL"):
                    signal = sig
                    best_asset = asset
                    pnl = _calc_simple_pnl(klines_1h, asset, signal, eng_capital)
                    break

            eng["last_signal"] = {"asset": best_asset, "signal": signal}
            eng["last_pnl"] = pnl
            _update_engine_stats(eng, pnl)

            results[engine_name] = {
                "signal": signal, "asset": best_asset,
                "pnl": pnl, "capital": eng_capital,
            }
        except Exception as e:
            results[engine_name] = {"error": str(e)}

    # ── 4. Totais do ciclo ────────────────────────────────────────────
    total_pnl = sum(r.get("pnl", 0) for r in results.values() if "pnl" in r)
    _lab_state["capital_virtual"] = round(capital + total_pnl, 2)
    _lab_state["total_cycles"] += 1
    _lab_state["last_feed_at"] = datetime.now().isoformat()

    cycle_record = {
        "cycle": _lab_state["total_cycles"],
        "timestamp": _lab_state["last_feed_at"],
        "regime": regime_info.get("regime", "UNKNOWN"),
        "total_pnl": total_pnl,
        "capital": _lab_state["capital_virtual"],
        "engines": results,
    }

    _lab_state["history"].append(cycle_record)
    if len(_lab_state["history"]) > _MAX_HISTORY:
        _lab_state["history"] = _lab_state["history"][-_MAX_HISTORY:]

    return cycle_record


def _update_engine_stats(eng: dict, pnl: float):
    """Atualiza win/loss/gain/loss do engine."""
    if pnl > 0:
        eng["win_count"] += 1
        eng["total_gain"] = round(eng["total_gain"] + pnl, 2)
    elif pnl < 0:
        eng["loss_count"] += 1
        eng["total_loss"] = round(eng["total_loss"] + abs(pnl), 2)
    eng["total_pnl"] = round(eng["total_pnl"] + pnl, 2)


def get_lab_results() -> dict:
    """Retorna estado completo do lab para o dashboard."""
    engines_summary = {}
    for name, eng in _lab_state["engines"].items():
        total = eng["win_count"] + eng["loss_count"]
        win_rate = round(eng["win_count"] / total * 100, 1) if total > 0 else 0.0
        engines_summary[name] = {
            "total_pnl": eng["total_pnl"],
            "win_count": eng["win_count"],
            "loss_count": eng["loss_count"],
            "win_rate": win_rate,
            "total_gain": eng["total_gain"],
            "total_loss": eng["total_loss"],
            "last_signal": eng["last_signal"],
            "last_pnl": eng["last_pnl"],
            "alloc_pct": _ENGINES.get(name, {}).get("alloc", 0) * 100,
        }

    return {
        "capital_virtual": _lab_state["capital_virtual"],
        "total_cycles": _lab_state["total_cycles"],
        "last_feed_at": _lab_state["last_feed_at"],
        "engines": engines_summary,
        "recent_history": _lab_state["history"][-20:],
    }


def reset_lab(capital: float = 500.0):
    """Reseta o lab para estado inicial."""
    _lab_state["capital_virtual"] = capital
    _lab_state["total_cycles"] = 0
    _lab_state["last_feed_at"] = None
    _lab_state["engines"] = {}
    _lab_state["history"] = []
