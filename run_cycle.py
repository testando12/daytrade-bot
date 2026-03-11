"""
Script standalone de ciclo de trading — executado pelo GitHub Actions.
Não precisa do servidor FastAPI rodando. Roda, salva resultado e sai.
"""

import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Garante que o diretório raiz está no path
sys.path.insert(0, str(Path(__file__).parent))

from app.engines import MomentumAnalyzer, RiskAnalyzer, PortfolioManager
from app.engines.market_scanner import MarketScanner
from app.engines.regime import RegimeDetector
from app.core.config import settings

DATA_DIR        = Path(__file__).parent / "data"
STATE_FILE      = DATA_DIR / "trade_state.json"
PERF_FILE       = DATA_DIR / "performance.json"
LOG_FILE        = DATA_DIR / "cycle_log.txt"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load(path: Path, default: dict) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return dict(default)


def _save(path: Path, obj: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _log(msg: str):
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        # Manter apenas as últimas 500 linhas
        lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
        if len(lines) > 500:
            LOG_FILE.write_text("\n".join(lines[-500:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _is_market_hours() -> bool:
    """Seg–Sex 10h–17h BRT (UTC-3)."""
    brt = timezone(timedelta(hours=-3))
    now = datetime.now(brt)
    return now.weekday() < 5 and 10 <= now.hour < 17


def _seconds_to_candle_close(interval_minutes: int = 5) -> float:
    """
    Retorna quantos segundos faltam para o fechamento do candle atual.

    Exemplo com interval=5: se agora é 14:03:20, o próximo close é 14:05:00
    → faltam 100 segundos.

    Usado para alinhar o ciclo ao fechamento exato do candle (evita
    entrar no meio do candle e gerar sinais prematuros).
    """
    now = datetime.now(timezone.utc)
    elapsed = (now.minute % interval_minutes) * 60 + now.second
    remaining = interval_minutes * 60 - elapsed
    return float(remaining)


async def wait_for_candle_close(interval_minutes: int = 5, tolerance_s: float = 2.0):
    """
    Aguarda o fechamento do próximo candle antes de rodar a análise.

    tolerance_s: executa se faltam menos de N segundos (evita espera logo
    após uma execução recém-concluída).
    """
    wait = _seconds_to_candle_close(interval_minutes)
    if wait > tolerance_s:
        _log(f"Alinhando ao candle {interval_minutes}m — aguardando {wait:.0f}s para o close")
        await asyncio.sleep(wait)
    else:
        _log(f"Candle {interval_minutes}m acabou de fechar — executando imediatamente")


# ── Ciclo principal ───────────────────────────────────────────────────────────

async def run_cycle():
    trade_state = _load(STATE_FILE, {
        "capital": settings.INITIAL_CAPITAL,
        "positions": {},
        "log": [],
        "total_pnl": 0.0,
        "last_cycle": None,
    })
    perf_state = _load(PERF_FILE, {
        "cycles": [],
        "total_pnl_history": [],
        "win_count": 0,
        "loss_count": 0,
        "best_day_pnl": 0.0,
        "worst_day_pnl": 0.0,
    })

    capital = trade_state.get("capital", settings.INITIAL_CAPITAL)
    _log(f"Iniciando ciclo — capital: R$ {capital:.2f}")

    # ── 1. Dados de mercado ──────────────────────────────────────────────────
    market_data = None
    data_source = "test"

    try:
        from app.market_data import market_data_service, MARKET_DATA_AVAILABLE
        if MARKET_DATA_AVAILABLE:
            klines = await market_data_service.get_all_klines(
                list(settings.ALL_ASSETS), "5m", 100
            )
            if klines:
                market_data = klines
                data_source = "yahoo.finance"
    except Exception as e:
        _log(f"Aviso: falha ao buscar dados reais ({e}), usando dados de teste")

    if not market_data:
        # Importa fallback do main
        from app.main import test_assets_data
        market_data = test_assets_data
        data_source = "test"

    _log(f"Dados obtidos: {len(market_data)} ativos via {data_source}")

    # ── 2. Market Scanner — filtra top candidatos ─────────────────────────────
    # Fase 1: triagem rápida (volume spike + price move) em todos os ativos.
    # Só os top 20 passam para análise de momentum completa.
    # Ativos de referência (BTC para IRQ) entram forçados se disponíveis.
    _FORCE_INCLUDE = [a for a in ("BTC", "PETR4", "SPY") if a in market_data]
    scanner_top, scan_scores = MarketScanner.scan(
        market_data, top_n=MarketScanner.TOP_N, force_include=_FORCE_INCLUDE
    )
    scan_summary = MarketScanner.summary(scan_scores, scanner_top)
    _log(
        f"Scanner: {scan_summary['total_assets']} ativos → "
        f"{scan_summary['selected']} selecionados "
        f"(↑{scan_summary['up']} ↓{scan_summary['down']}) | "
        f"top5: {scan_summary['top5']}"
    )
    # Filtra market_data para apenas os candidatos selecionados
    market_data_filtered = {a: market_data[a] for a in scanner_top if a in market_data}

    # ── 3. Momentum (só nos candidatos do scanner) ────────────────────────────
    momentum_results = MomentumAnalyzer.calculate_multiple_assets(market_data_filtered)
    if not momentum_results:
        _log("ERRO: nenhum resultado de momentum")
        return

    momentum_scores = {a: d["momentum_score"] for a, d in momentum_results.items()}

    # ── 4. Risco ─────────────────────────────────────────────────────────────
    # Usa BTC ou primeiro ativo disponível como referência para o IRQ
    ref = "BTC" if "BTC" in market_data else list(market_data.keys())[0]
    risk_analysis = RiskAnalyzer.calculate_irq(
        market_data[ref].get("prices", []),
        market_data[ref].get("volumes", []),
    )
    irq_score  = risk_analysis["irq_score"]
    protection = RiskAnalyzer.get_protection_level(irq_score)

    # ── 4b. Regime Detection — ADX + ATR Ratio + Hurst Exponent ─────────────
    _regime_result = RegimeDetector.detect(market_data[ref].get("prices", []))
    _log(
        f"Regime: {_regime_result['regime']} "
        f"(conf: {_regime_result.get('confidence', 0):.2f} | "
        f"dir: {_regime_result.get('direction', '?')} | "
        f"ADX: {_regime_result.get('adx', 0):.1f} | "
        f"ATR_ratio: {_regime_result.get('atr_ratio', 0):.2f} | "
        f"Hurst: {_regime_result.get('hurst', 0):.3f})"
    )

    # Ajusta scores de momentum pelo regime detetado
    # apply_multipliers espera {strategy: cap} — adaptamos com os nomes dos ativos
    _regime_caps = RegimeDetector.apply_multipliers(
        {a: abs(s) for a, s in momentum_scores.items()}, _regime_result
    )
    # Preserva sinal original mas escala pela magnitude ajustada pelo regime
    momentum_scores_regime = {
        a: s * (_regime_caps.get(a, abs(s)) / abs(s) if s != 0 else 1.0)
        for a, s in momentum_scores.items()
    }

    # ── 5. Alocação ──────────────────────────────────────────────────────────
    allocation  = PortfolioManager.calculate_portfolio_allocation(
        momentum_scores_regime, irq_score, capital,
        momentum_details=momentum_results,
    )
    rebalancing = PortfolioManager.apply_rebalancing_rules(
        allocation, momentum_results, capital, irq_score,
    )

    # ── 6. Registrar posições e P&L ──────────────────────────────────────────
    new_positions = {}
    cycle_pnl     = 0.0
    buys, sells = 0, 0

    prev_positions = trade_state.get("positions", {})

    for asset, alloc in rebalancing.items():
        action     = alloc.get("action", "HOLD")
        rec_amount = alloc.get("recommended_amount", 0)
        # Usa posição anterior salva (capital alocado no ciclo anterior)
        cur_amount = prev_positions.get(asset, {}).get("amount", 0) \
                     if isinstance(prev_positions.get(asset), dict) \
                     else prev_positions.get(asset, 0)

        # Se não havia posição e há alocação recomendada → é uma compra
        if action == "HOLD" and rec_amount > 0 and cur_amount == 0:
            action = "BUY"
        # Se havia posição e a recomendação é 0 → é uma venda
        elif action == "HOLD" and rec_amount == 0 and cur_amount > 0:
            action = "SELL"

        new_positions[asset] = {
            "amount":         round(rec_amount, 2),
            "action":         action,
            "pct":            round(rec_amount / capital * 100, 1) if capital > 0 else 0,
            "classification": alloc.get("classification", "—"),
        }

        if action == "BUY" and rec_amount > cur_amount:
            cycle_pnl += (rec_amount - cur_amount)
            buys += 1
        elif action == "SELL" and cur_amount > rec_amount:
            cycle_pnl -= (cur_amount - rec_amount)
            sells += 1

    # Salvar estado
    trade_state["positions"]  = new_positions
    trade_state["last_cycle"] = datetime.now().isoformat()
    trade_state["total_pnl"]  = round(trade_state.get("total_pnl", 0.0) + cycle_pnl, 4)

    # Log de evento
    event = {
        "timestamp": datetime.now().isoformat(),
        "type": "CICLO",
        "asset": "—",
        "amount": round(capital, 2),
        "note": (f"🔄 GitHub Actions — {len(rebalancing)} ativos | "
                 f"IRQ: {irq_score:.3f} | {protection['level']} | "
                 f"Regime: {_regime_result['regime']} | "
                 f"BUY:{buys} SELL:{sells}"),
    }
    trade_state.setdefault("log", []).insert(0, event)
    trade_state["log"] = trade_state["log"][:200]

    # Performance
    perf_state.setdefault("cycles", []).append({
        "timestamp": datetime.now().isoformat(),
        "pnl":     round(cycle_pnl, 4),
        "capital": round(capital, 2),
        "irq":     round(irq_score, 4),
    })
    perf_state["cycles"]             = perf_state["cycles"][-500:]
    perf_state.setdefault("total_pnl_history", []).append(round(capital, 2))
    perf_state["total_pnl_history"]  = perf_state["total_pnl_history"][-500:]

    if cycle_pnl > 0:
        perf_state["win_count"] = perf_state.get("win_count", 0) + 1
    elif cycle_pnl < 0:
        perf_state["loss_count"] = perf_state.get("loss_count", 0) + 1

    _save(STATE_FILE, trade_state)
    _save(PERF_FILE,  perf_state)

    _log(
        f"Ciclo concluído | IRQ: {irq_score:.3f} ({protection['level']}) | "
        f"BUY:{buys} SELL:{sells} | P&L estimado: R$ {cycle_pnl:.4f} | "
        f"Total P&L: R$ {trade_state['total_pnl']:.4f}"
    )

    # Mostrar top alocações
    top = sorted(
        [(a, v["amount"]) for a, v in new_positions.items() if v["amount"] > 0],
        key=lambda x: x[1], reverse=True
    )[:5]
    if top:
        _log("Top posições: " + " | ".join(f"{a}=R${v:.2f}" for a, v in top))

    return {"irq": irq_score, "cycle_pnl": cycle_pnl, "buys": buys, "sells": sells}


if __name__ == "__main__":
    result = asyncio.run(run_cycle())
    if result is None:
        sys.exit(1)
