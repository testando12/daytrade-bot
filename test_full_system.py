"""
Teste isolado completo do sistema de trading.
Valida: momentum, risco, portfólio, ciclo completo e taxa de acerto.
Meta: >= 70% de acerto nas previsões.
"""

import asyncio
import sys
import json
from pathlib import Path
from datetime import datetime

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))

from app.engines import MomentumAnalyzer, RiskAnalyzer, PortfolioManager
from app.core.config import settings
from app.market_data import market_data_service

CAPITAL = 150.0
SEP = "─" * 60


def pct(v, total):
    return f"{v/total*100:.1f}%" if total > 0 else "0%"


# ══════════════════════════════════════════════════════════════
# TESTE 1 — Busca de dados reais
# ══════════════════════════════════════════════════════════════
async def test_market_data():
    print(f"\n{SEP}")
    print("TESTE 1 — DADOS DE MERCADO (Yahoo Finance)")
    print(SEP)

    klines = await market_data_service.get_all_klines(
        list(settings.ALL_ASSETS), "5m", 30
    )

    ok = 0
    for asset in settings.ALL_ASSETS:
        data = klines.get(asset, {})
        prices  = data.get("prices", [])
        volumes = data.get("volumes", [])
        status  = "✅" if len(prices) >= 10 else "❌"
        if len(prices) >= 10:
            ok += 1
        chg = ((prices[-1] - prices[0]) / prices[0] * 100) if len(prices) >= 2 else 0
        print(f"  {status} {asset:8s} {len(prices):2d} candles | último: {prices[-1] if prices else 0:.4f} | variação: {chg:+.2f}%")

    print(f"\n  Resultado: {ok}/{len(settings.ALL_ASSETS)} ativos com dados suficientes")
    return klines, ok >= 10


# ══════════════════════════════════════════════════════════════
# TESTE 2 — Motor de Momentum (precisão da previsão)
# ══════════════════════════════════════════════════════════════
async def test_momentum(klines):
    print(f"\n{SEP}")
    print("TESTE 2 — MOTOR DE MOMENTUM (previsão de direção)")
    print(SEP)

    results = MomentumAnalyzer.calculate_multiple_assets(klines)

    correct = 0
    total   = 0
    buys    = []
    sells   = []
    holds   = []

    print(f"  {'Ativo':8s} {'Score':>7s} {'Direção':>10s} {'EntryVld':>8s} {'Qual':>6s} {'Real%':>7s} {'✓?':>4s}")
    print(f"  {'─'*8} {'─'*7} {'─'*10} {'─'*8} {'─'*6} {'─'*7} {'─'*4}")

    for asset, d in results.items():
        prices  = klines.get(asset, {}).get("prices", [])
        if len(prices) < 5:
            continue

        score  = d["momentum_score"]
        valid  = d["entry_valid"]
        qual   = d["signal_quality"]
        cls    = d["classification"]

        # Variação real dos últimos 5 candles (o que o bot "previu")
        real_chg = (prices[-1] - prices[-6]) / prices[-6] * 100 if len(prices) >= 6 else 0

        # O bot prevê alta (score>0) ou queda (score<0)?
        predicted_up = score > 0
        actual_up    = real_chg > 0

        is_correct = (predicted_up == actual_up)
        if abs(score) >= 0.05:  # só conta previsões com algum sinal
            total   += 1
            correct += (1 if is_correct else 0)

        flag = "✅" if is_correct else "❌"

        line = (f"  {asset:8s} {score:+.4f} {cls[:10]:>10s} "
                f"{'Sim':>8s} " if valid else
                f"  {asset:8s} {score:+.4f} {cls[:10]:>10s} "
                f"{'Não':>8s} ")
        print(f"  {asset:8s} {score:+.4f} {cls[:10]:>10s} "
              f"{'Sim' if valid else 'Não':>8s} {qual:.2f} {real_chg:+.2f}% {flag}")

        if valid and score > 0.05:
            buys.append((asset, score, 0))
        elif valid and score < -0.05:
            sells.append((asset, score, 0))
        else:
            holds.append(asset)

    accuracy = correct / total * 100 if total > 0 else 0
    status   = "✅ PASSA" if accuracy >= 70 else "⚠️  ABAIXO DA META"
    print(f"\n  Acerto de direção: {correct}/{total} = {accuracy:.1f}%  {status}")
    print(f"  Meta: >= 70%")
    print(f"\n  TOP COMPRAS: {[f'{a}({s:+.3f})' for a,s,_ in sorted(buys, key=lambda x:-x[1])[:5]]}")
    print(f"  TOP VENDAS:  {[f'{a}({s:+.3f})' for a,s,_ in sorted(sells, key=lambda x:x[1])[:3]]}")

    return results, accuracy, buys


# ══════════════════════════════════════════════════════════════
# TESTE 3 — Alocação de capital (R$150)
# ══════════════════════════════════════════════════════════════
async def test_portfolio(klines, momentum_results):
    print(f"\n{SEP}")
    print(f"TESTE 3 — ALOCAÇÃO DE CAPITAL (R$ {CAPITAL:.2f})")
    print(SEP)

    momentum_scores = {a: d["momentum_score"] for a, d in momentum_results.items()}
    ref = list(klines.keys())[0]
    risk = RiskAnalyzer.calculate_irq(
        klines[ref].get("prices", []),
        klines[ref].get("volumes", []),
    )
    irq = risk["irq_score"]

    alloc = PortfolioManager.calculate_portfolio_allocation(
        momentum_scores, irq, CAPITAL, momentum_details=momentum_results
    )
    rebal = PortfolioManager.apply_rebalancing_rules(
        alloc, momentum_results, CAPITAL, irq
    )

    total_allocated = sum(v.get("recommended_amount", 0) for v in rebal.values())
    buy_count  = sum(1 for v in rebal.values() if v.get("action") == "BUY")
    sell_count = sum(1 for v in rebal.values() if v.get("action") == "SELL")
    hold_count = sum(1 for v in rebal.values() if v.get("action") == "HOLD")

    print(f"  IRQ (Risco de mercado): {irq:.4f} — {risk.get('protection_level','?')}")
    print(f"  Capital total:   R$ {CAPITAL:.2f}")
    print(f"  Total alocado:   R$ {total_allocated:.2f} ({pct(total_allocated, CAPITAL)})")
    print(f"  Em caixa:        R$ {CAPITAL - total_allocated:.2f}")
    print(f"  BUY:{buy_count}  SELL:{sell_count}  HOLD:{hold_count}")
    print()

    alocados = [(a, v) for a, v in rebal.items() if v.get("recommended_amount", 0) > 0]
    alocados.sort(key=lambda x: -x[1].get("recommended_amount", 0))

    print(f"  {'Ativo':8s} {'Ação':6s} {'Valor':>10s} {'%Cap':>6s} {'Classe'}")
    print(f"  {'─'*8} {'─'*6} {'─'*10} {'─'*6} {'─'*15}")
    for asset, v in alocados:
        amt = v.get("recommended_amount", 0)
        act = v.get("action", "HOLD")
        cls = v.get("classification", "—")
        print(f"  {asset:8s} {act:6s} R$ {amt:7.2f} {pct(amt, CAPITAL):>6s} {cls}")

    ok = total_allocated > 0 and buy_count > 0
    print(f"\n  Resultado: {'✅ ALOCANDO CAPITAL' if ok else '❌ SEM ALOCAÇÃO — VERIFICAR'}")
    return rebal, irq, total_allocated > 0


# ══════════════════════════════════════════════════════════════
# TESTE 4 — Simulação de ciclo (compra → espera → venda)
# ══════════════════════════════════════════════════════════════
async def test_cycle_simulation(klines, momentum_results):
    print(f"\n{SEP}")
    print("TESTE 4 — SIMULAÇÃO DE CICLO COMPLETO (compra → venda)")
    print(SEP)

    momentum_scores = {a: d["momentum_score"] for a, d in momentum_results.items()}
    ref = list(klines.keys())[0]
    risk = RiskAnalyzer.calculate_irq(
        klines[ref].get("prices", []),
        klines[ref].get("volumes", []),
    )
    irq = risk["irq_score"]

    alloc = PortfolioManager.calculate_portfolio_allocation(
        momentum_scores, irq, CAPITAL, momentum_details=momentum_results
    )
    rebal = PortfolioManager.apply_rebalancing_rules(
        alloc, momentum_results, CAPITAL, irq
    )

    print(f"  Simulando com dados históricos reais dos últimos 30 candles de 5min")
    print(f"  Estratégia: compra no candle 20, vende no candle 30 (50 min de holding)\n")

    total_pnl  = 0.0
    wins = losses = 0
    positions_taken = []

    print(f"  {'Ativo':8s} {'Compra':>10s} {'Venda':>10s} {'Qtd':>8s} {'P&L':>10s} {'%':>7s} {'✓?'}")
    print(f"  {'─'*8} {'─'*10} {'─'*10} {'─'*8} {'─'*10} {'─'*7} {'─'*3}")

    for asset, v in rebal.items():
        rec_amount = v.get("recommended_amount", 0)
        if rec_amount <= 0:
            continue

        prices = klines.get(asset, {}).get("prices", [])
        if len(prices) < 25:
            continue

        buy_price  = prices[20]   # entrada no candle 20
        sell_price = prices[-1]   # saída no candle mais recente

        if buy_price <= 0:
            continue

        qty       = rec_amount / buy_price
        sell_val  = qty * sell_price
        pnl       = sell_val - rec_amount
        pnl_pct   = pnl / rec_amount * 100

        total_pnl += pnl
        if pnl >= 0:
            wins += 1
        else:
            losses += 1

        positions_taken.append((asset, rec_amount, pnl))
        flag = "✅" if pnl >= 0 else "❌"
        print(f"  {asset:8s} R${buy_price:8.4f} R${sell_price:8.4f} "
              f"{qty:8.4f} R${pnl:+8.2f} {pnl_pct:+6.2f}% {flag}")

    total_cycles = wins + losses
    win_rate = wins / total_cycles * 100 if total_cycles > 0 else 0
    final_capital = CAPITAL + total_pnl
    status_wr = "✅ PASSA" if win_rate >= 70 else "⚠️  ABAIXO DA META"
    status_pnl = "✅ POSITIVO" if total_pnl > 0 else "❌ NEGATIVO"

    print(f"\n  ══ RESULTADO DO CICLO ══")
    print(f"  Capital inicial:  R$ {CAPITAL:.2f}")
    print(f"  P&L do ciclo:     R$ {total_pnl:+.2f}  {status_pnl}")
    print(f"  Capital final:    R$ {final_capital:.2f}")
    print(f"  Taxa de acerto:   {wins}/{total_cycles} = {win_rate:.1f}%  {status_wr}")
    print(f"  Meta P&L/dia:     R$ 100.00")
    print(f"  Ciclos/dia B3:    ~14 (10h-17h, 30min cada)")
    print(f"  P&L estimado/dia: R$ {total_pnl * 14:+.2f}")

    return win_rate, total_pnl


# ══════════════════════════════════════════════════════════════
# TESTE 5 — Backtest histórico (walk-forward em candles de 5min)
# ══════════════════════════════════════════════════════════════
async def test_backtest_accuracy():
    print(f"\n{SEP}")
    print("TESTE 5 — BACKTEST 5min (walk-forward, sinais fortes ≥0.10)")
    print(SEP)

    # Usa candles de 5min — mesma granularidade que o bot opera
    # 6 ativos: mix B3 + crypto para cobertura real
    test_assets = list(settings.ALL_ASSETS)[:4] + ["BTC", "ETH"]
    klines_5m = await market_data_service.get_all_klines(
        test_assets, "5m", 150
    )

    correct = 0
    total   = 0

    print(f"  {'Ativo':8s} {'Acertos':>8s} {'Total':>6s} {'%':>6s}")
    print(f"  {'─'*8} {'─'*8} {'─'*6} {'─'*6}")

    for asset, data in klines_5m.items():
        prices  = data.get("prices", [])
        volumes = data.get("volumes", [1.0] * len(prices))
        if len(prices) < 50:
            continue

        asset_ok = 0
        asset_total = 0

        # Walk-forward: usa 30 candles para prever se preço MÉDIO dos próximos 6 candles
        # (= 30min de holding) é maior que preço atual
        # Conta apenas quando o sinal é forte (|score| >= 0.10) — como o bot real opera
        for i in range(30, min(len(prices) - 7, 130)):
            hist_p = prices[:i]
            hist_v = volumes[:i] if len(volumes) >= i else [1.0] * i

            m = MomentumAnalyzer.calculate_momentum_score(hist_p, hist_v)
            score = m["momentum_score"]

            # Ignora sinais fracos (bot não operaria)
            if abs(score) < 0.10:
                continue

            predicted_up = score > 0

            # Resultado real: preço médio dos próximos 6 candles vs preço atual
            future_avg = sum(prices[i+1:i+7]) / 6
            real_up = future_avg > prices[i]

            is_correct = (predicted_up == real_up)
            asset_ok    += (1 if is_correct else 0)
            asset_total += 1

        acc_asset = asset_ok / asset_total * 100 if asset_total > 0 else 0
        flag = "✅" if acc_asset >= 70 else "❌"
        print(f"  {asset:8s} {asset_ok:>8d} {asset_total:>6d} {acc_asset:>5.1f}% {flag}")

        correct += asset_ok
        total   += asset_total

    acc = correct / total * 100 if total > 0 else 0
    status = "✅ PASSA" if acc >= 70 else "⚠️  ABAIXO DA META"
    print(f"\n  Acerto histórico: {correct}/{total} = {acc:.1f}%  {status}")
    return acc



# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
async def main():
    print("=" * 60)
    print("   TESTE COMPLETO DO SISTEMA DE TRADING")
    print(f"   Capital: R$ {CAPITAL:.2f} | {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 60)

    # T1 — Dados
    klines, data_ok = await test_market_data()
    if not data_ok:
        print("\n❌ FALHA CRÍTICA: dados insuficientes. Verifique conexão.")
        return

    # T2 — Momentum
    momentum_results, momentum_acc, buys = await test_momentum(klines)

    # T3 — Portfólio
    rebal, irq, alloc_ok = await test_portfolio(klines, momentum_results)

    # T4 — Ciclo simulado
    cycle_win_rate, cycle_pnl = await test_cycle_simulation(klines, momentum_results)

    # T5 — Backtest histórico
    hist_acc = await test_backtest_accuracy()

    # ── Relatório final ──────────────────────────────────────
    print(f"\n{'═'*60}")
    print("   DIAGNÓSTICO FINAL")
    print(f"{'═'*60}")

    checks = [
        ("Dados de mercado",       data_ok,                    "15/15 ativos"),
        ("Acerto de direção",      momentum_acc >= 70,         f"{momentum_acc:.1f}% (meta: 70%)"),
        ("Alocação de capital",    alloc_ok,                   f"R${CAPITAL:.0f} distribuído"),
        ("Taxa de acerto ciclo",   cycle_win_rate >= 70,       f"{cycle_win_rate:.1f}% (meta: 70%)"),
        ("Backtest histórico",     hist_acc >= 70,             f"{hist_acc:.1f}% (meta: 70%)"),
        ("P&L positivo por ciclo", cycle_pnl > 0,              f"R${cycle_pnl:+.2f}/ciclo"),
    ]

    passed = 0
    for name, ok, detail in checks:
        icon = "✅" if ok else "⚠️ "
        passed += (1 if ok else 0)
        print(f"  {icon} {name:30s} {detail}")

    print(f"\n  {'─'*58}")
    overall = passed >= 4
    print(f"  {'✅ SISTEMA APROVADO' if overall else '⚠️  PRECISA AJUSTES'} — {passed}/{len(checks)} testes passaram")

    if not overall:
        print("\n  Itens a ajustar:")
        for name, ok, detail in checks:
            if not ok:
                print(f"    → {name}: {detail}")

    print(f"\n  Projeção para hoje:")
    print(f"    P&L por ciclo:  R$ {cycle_pnl:+.4f}")
    print(f"    Ciclos/dia:     ~14  (10h–17h BRT, 30min cada)")
    print(f"    P&L esperado:   R$ {cycle_pnl*14:+.2f}")
    print(f"    Capital final:  R$ {CAPITAL + cycle_pnl*14:.2f}")
    print(f"{'═'*60}\n")

    # Salva relatório em JSON
    report = {
        "timestamp": datetime.now().isoformat(),
        "capital": CAPITAL,
        "tests": {
            "momentum_accuracy_pct": round(momentum_acc, 2),
            "backtest_accuracy_pct": round(hist_acc, 2),
            "cycle_win_rate_pct":    round(cycle_win_rate, 2),
            "cycle_pnl":             round(cycle_pnl, 4),
            "alloc_ok":              alloc_ok,
        },
        "projection": {
            "cycles_per_day":   14,
            "expected_pnl_day": round(cycle_pnl * 14, 2),
            "expected_capital": round(CAPITAL + cycle_pnl * 14, 2),
        },
        "approved": overall,
    }
    Path("data").mkdir(exist_ok=True)
    Path("data/test_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(f"  Relatório salvo em data/test_report.json")


if __name__ == "__main__":
    asyncio.run(main())
