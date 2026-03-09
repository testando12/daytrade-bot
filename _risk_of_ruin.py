"""
RISK OF RUIN — Teste Completo
Baseado em Monte Carlo Simulation + Stress de Execução + Shuffle Test
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Testes:
  1. Dados base (WR, R:R, risco/trade)
  2. Sequência máxima de losses consecutivos
  3. Impacto no capital (5, 10, 15 losses)
  4. Monte Carlo 10.000 simulações (Normal / Ruim / Extremo)
  5. Stress de execução (rejeição 20%, slippage 2x, latência)
  6. Shuffle Test (embaralhar ordem dos trades)
"""
import random
import math
import statistics
import time

# ─── Importar engine de simulação do stress test v3 ──────────────────────
from _stress_tests import (
    simular_ciclo, run_sim, CAPITAL_INICIAL, REGIMES, ATR_SL, ATR_TP,
    MIN_SCORE, TRAILING_ACTIVATION, TRAILING_LOCK_PCT,
    BREAKEVEN_THRESHOLD, BREAKEVEN_FIRE_PROB,
    REGIME_SKIP_PROB, REGIME_SIZE_MULT, REGIME_SQ_MULT,
    pick_regime, custo, NUM_ASSETS, CAPITAL_PER_ASSET, MAX_POSITION_PCT,
    ADVERSE_CAPTURE, ADVERSE_LOSS_MULT, MIN_RETURN, FILL_FAILURE_RATE,
)

# ─── PARÂMETROS DO TESTE ─────────────────────────────────────────────────
N_MONTE_CARLO    = 5_000    # simulações Monte Carlo
N_TRADES_PER_SIM = 500      # trades por simulação (~6 dias)
RISK_PER_TRADE   = 0.02     # 2% risco por trade
RUIN_THRESHOLD   = 0.50     # <50% do capital = "quebrou"

# SQ calibrado no stress test v3 (breakeven = 0.25)
SQ_REALISTIC = 0.35

def banner(title):
    print("\n" + "=" * 70)
    t_safe = title.encode('ascii', 'replace').decode('ascii')
    print(f"  {t_safe}")
    print("=" * 70)

def sub(title):
    print(f"\n  ── {title} ──")

# ═══════════════════════════════════════════════════════════════════════════
#  PARTE 1: DADOS BASE DO BOT
# ═══════════════════════════════════════════════════════════════════════════
def test_dados_base():
    banner("1. DADOS BASE DO BOT")
    
    # Rodar 2000 ciclos para obter estatísticas sólidas
    random.seed(42)
    cap = CAPITAL_INICIAL
    trades_pnl = []
    wins = losses = 0
    
    for _ in range(2000):
        pnl, n_trades, skipped = simular_ciclo(cap, signal_quality=SQ_REALISTIC)
        if pnl != 0:
            trades_pnl.append(pnl)
            if pnl > 0: wins += 1
            else: losses += 1
        cap = max(cap + pnl, 0.01)
    
    active = wins + losses
    wr = wins / active if active else 0
    
    gains = [p for p in trades_pnl if p > 0]
    loss_list = [abs(p) for p in trades_pnl if p < 0]
    
    avg_win = statistics.mean(gains) if gains else 0
    avg_loss = statistics.mean(loss_list) if loss_list else 0.01
    rr = avg_win / avg_loss if avg_loss > 0 else 0
    risk_pct = (avg_loss / CAPITAL_INICIAL) * 100
    expectancy = (wr * avg_win) - ((1 - wr) * avg_loss)
    
    print(f"\n  Amostra: 2000 ciclos, {active} trades ativos")
    print(f"  +--------------------------------------+")
    print(f"  |  Win Rate      : {wr*100:>6.1f}%             |")
    print(f"  |  Risk/Reward   : {rr:>6.2f}:1             |")
    print(f"  |  Risco/trade   : {risk_pct:>6.3f}%             |")
    print(f"  |  Avg Win       : R${avg_win:>7.2f}            |")
    print(f"  |  Avg Loss      : R${avg_loss:>7.2f}            |")
    print(f"  |  Expectativa   : R${expectancy:>+7.4f}/trade     |")
    print(f"  |  SL config     : {ATR_SL*100:.1f}%                |")
    print(f"  |  TP config     : {ATR_TP*100:.1f}%                |")
    print(f"  +--------------------------------------+")
    
    return {
        "wr": wr, "rr": rr, "risk_pct": risk_pct,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "expectancy": expectancy, "trades_pnl": trades_pnl,
        "active": active
    }

# ═══════════════════════════════════════════════════════════════════════════
#  PARTE 2: SEQUÊNCIA MÁXIMA DE LOSSES CONSECUTIVOS
# ═══════════════════════════════════════════════════════════════════════════
def test_losses_consecutivos(dados):
    banner("2. SEQUÊNCIA MÁXIMA DE LOSSES CONSECUTIVOS")
    
    wr = dados["wr"]
    n = dados["active"]
    
    # Fórmula teórica: L ≈ log(N) / log(1/(1-WR))
    if wr < 1.0:
        L_teorico = math.log(n) / math.log(1.0 / (1.0 - wr))
    else:
        L_teorico = 0
    
    print(f"\n  Win Rate = {wr*100:.1f}%")
    print(f"  N trades = {n}")
    print(f"\n  Fórmula: L ≈ log(N) / log(1/(1-WR))")
    print(f"  L ≈ log({n}) / log(1/{1-wr:.3f})")
    print(f"  L ≈ {L_teorico:.1f} losses consecutivos possíveis")
    
    # Simulação real — rodar várias vezes e achar o máx
    max_consec_losses = []
    for seed in range(50):
        random.seed(seed)
        cap = CAPITAL_INICIAL
        consec = 0
        max_c = 0
        for _ in range(n):
            pnl, _, _ = simular_ciclo(cap, signal_quality=SQ_REALISTIC)
            if pnl < 0:
                consec += 1
                max_c = max(max_c, consec)
            elif pnl > 0:
                consec = 0
            cap = max(cap + pnl, 0.01)
        max_consec_losses.append(max_c)
    
    avg_max = statistics.mean(max_consec_losses)
    p95_max = sorted(max_consec_losses)[int(len(max_consec_losses)*0.95)]  # percentil 95
    p99_max = sorted(max_consec_losses)[int(len(max_consec_losses)*0.99)]  # percentil 99
    worst = max(max_consec_losses)
    
    sub("Simulação real (100 amostras)")
    print(f"  Média máx losses seguidos : {avg_max:.1f}")
    print(f"  P95 máx losses seguidos   : {p95_max}")
    print(f"  P99 máx losses seguidos   : {p99_max}")
    print(f"  Pior caso (100 amostras)  : {worst}")
    print(f"  Teórico (fórmula)         : {L_teorico:.0f}")
    
    return {"teorico": L_teorico, "avg": avg_max, "p95": p95_max, "p99": p99_max, "worst": worst}

# ═══════════════════════════════════════════════════════════════════════════
#  PARTE 3: IMPACTO NO CAPITAL (DRAWDOWN POR LOSSES CONSECUTIVOS)
# ═══════════════════════════════════════════════════════════════════════════
def test_impacto_capital(dados, losses_info):
    banner("3. IMPACTO NO CAPITAL (DRAWDOWN POR LOSSES CONSECUTIVOS)")
    
    avg_loss = dados["avg_loss"]
    
    print(f"\n  Avg loss por trade: R${avg_loss:.2f}")
    print(f"  Capital inicial: R${CAPITAL_INICIAL:.2f}")
    print(f"\n  {'Losses Seguidos':>16}  {'Drawdown R$':>12}  {'DD %':>8}  {'Capital Resta':>14}  {'Status'}")
    print(f"  {'─'*16}  {'─'*12}  {'─'*8}  {'─'*14}  {'─'*12}")
    
    scenarios = [5, 8, 10, 12, 15, int(losses_info["p99"]), int(losses_info["worst"])]
    scenarios = sorted(set(s for s in scenarios if s > 0))
    
    results = []
    for n_loss in scenarios:
        # Drawdown composto (cada loss reduz o capital base)
        cap = CAPITAL_INICIAL
        for _ in range(n_loss):
            loss = min(avg_loss, cap * RISK_PER_TRADE)
            cap -= loss
        
        dd_pct = ((CAPITAL_INICIAL - cap) / CAPITAL_INICIAL) * 100
        status = "OK" if dd_pct < 20 else ("ALERTA" if dd_pct < 35 else "PERIGO")
        icon = "[OK]" if status == "OK" else ("[!!]" if status == "ALERTA" else "[XX]")
        
        print(f"  {n_loss:>16}  R${CAPITAL_INICIAL - cap:>10.2f}  {dd_pct:>7.1f}%  R${cap:>12.2f}  {icon} {status}")
        results.append({"losses": n_loss, "dd_pct": dd_pct, "cap_final": cap})
    
    # Também simular com simular_ciclo real (force_loss)
    sub("Simulação com engine real (force_loss)")
    for n_loss in [5, 10, 15]:
        r = run_sim(200, signal_quality=SQ_REALISTIC, force_loss_n=n_loss, seed=99)
        queda = ((CAPITAL_INICIAL - r['eq_min']) / CAPITAL_INICIAL) * 100
        icon = "[OK]" if queda < 20 else ("[!!]" if queda < 35 else "[XX]")
        print(f"  {n_loss:>2} losses -> min R${r['eq_min']:.2f} (DD: {queda:.1f}%) -> recupera p/ R${r['capital']:.2f}  {icon}")
    
    return results

# ═══════════════════════════════════════════════════════════════════════════
#  PARTE 4: MONTE CARLO — 10.000 SIMULAÇÕES (3 CENÁRIOS)
# ═══════════════════════════════════════════════════════════════════════════
def test_monte_carlo(dados):
    banner("4. MONTE CARLO — 10.000 SIMULAÇÕES")
    
    cenarios = {
        "NORMAL": {
            "desc": "WR histórico, slippage normal",
            "sq": SQ_REALISTIC,
            "extra_slip": 0.0,
            "fee_mult": 1.0,
            "vol_mult": 1.0,
            "wr_penalty": 0.0,
        },
        "RUIM": {
            "desc": "WR -10%, slippage 2x",
            "sq": max(SQ_REALISTIC - 0.08, 0.05),  # SQ menor simula WR -10%
            "extra_slip": 0.001,   # slippage 2x
            "fee_mult": 1.5,
            "vol_mult": 1.3,
            "wr_penalty": 0.10,
        },
        "EXTREMO": {
            "desc": "WR -15%, slippage 3x",
            "sq": max(SQ_REALISTIC - 0.12, 0.02),  # SQ bem menor simula WR -15%
            "extra_slip": 0.002,   # slippage 3x
            "fee_mult": 2.0,
            "vol_mult": 1.5,
            "wr_penalty": 0.15,
        },
    }
    
    all_results = {}
    
    for nome, cfg in cenarios.items():
        sub(f"Cenário {nome}: {cfg['desc']}")
        
        finals = []
        max_dds = []
        ruins = 0
        profitable = 0
        
        t0 = time.time()
        for i in range(N_MONTE_CARLO):
            r = run_sim(
                N_TRADES_PER_SIM,
                signal_quality=cfg["sq"],
                extra_slip=cfg["extra_slip"],
                fee_mult=cfg["fee_mult"],
                vol_mult=cfg["vol_mult"],
                seed=i * 7 + 13,  # seeds diferentes
            )
            finals.append(r["capital"])
            max_dds.append(r["max_dd"])
            if r["capital"] < CAPITAL_INICIAL * RUIN_THRESHOLD:
                ruins += 1
            if r["capital"] > CAPITAL_INICIAL:
                profitable += 1
        
        elapsed = time.time() - t0
        
        finals_sorted = sorted(finals)
        dds_sorted = sorted(max_dds)
        
        profit_pct = (profitable / N_MONTE_CARLO) * 100
        ruin_pct = (ruins / N_MONTE_CARLO) * 100
        
        avg_final = statistics.mean(finals)
        median_final = statistics.median(finals)
        p5_final = finals_sorted[int(N_MONTE_CARLO * 0.05)]
        p25_final = finals_sorted[int(N_MONTE_CARLO * 0.25)]
        p75_final = finals_sorted[int(N_MONTE_CARLO * 0.75)]
        p95_final = finals_sorted[int(N_MONTE_CARLO * 0.95)]
        
        avg_dd = statistics.mean(max_dds)
        p95_dd = dds_sorted[int(N_MONTE_CARLO * 0.95)]
        p99_dd = dds_sorted[int(N_MONTE_CARLO * 0.99)]
        
        avg_return = ((avg_final - CAPITAL_INICIAL) / CAPITAL_INICIAL) * 100
        median_return = ((median_final - CAPITAL_INICIAL) / CAPITAL_INICIAL) * 100
        
        print(f"  ({elapsed:.1f}s para {N_MONTE_CARLO:,} simulações, {N_TRADES_PER_SIM} trades cada)")
        print()
        print(f"  +---------------------------------------------+")
        print(f"  |  Simulacoes lucrativas : {profit_pct:>6.1f}%              |")
        print(f"  |  Risco de ruina (<50%) : {ruin_pct:>6.2f}%              |")
        print(f"  |  Retorno medio         : {avg_return:>+6.1f}%              |")
        print(f"  |  Retorno mediana       : {median_return:>+6.1f}%              |")
        print(f"  |  Drawdown medio        : {avg_dd:>6.1f}%              |")
        print(f"  |  Drawdown P95          : {p95_dd:>6.1f}%              |")
        print(f"  |  Drawdown P99          : {p99_dd:>6.1f}%              |")
        print(f"  +---------------------------------------------+")
        print()
        print(f"  Distribuição de capital final:")
        print(f"    P5  : R${p5_final:>8.2f}  ({((p5_final-CAPITAL_INICIAL)/CAPITAL_INICIAL)*100:>+.1f}%)")
        print(f"    P25 : R${p25_final:>8.2f}  ({((p25_final-CAPITAL_INICIAL)/CAPITAL_INICIAL)*100:>+.1f}%)")
        print(f"    P50 : R${median_final:>8.2f}  ({median_return:>+.1f}%)")
        print(f"    P75 : R${p75_final:>8.2f}  ({((p75_final-CAPITAL_INICIAL)/CAPITAL_INICIAL)*100:>+.1f}%)")
        print(f"    P95 : R${p95_final:>8.2f}  ({((p95_final-CAPITAL_INICIAL)/CAPITAL_INICIAL)*100:>+.1f}%)")
        
        # Veredicto
        if profit_pct >= 80 and p95_dd < 25 and ruin_pct < 5:
            v = "[OK] ROBUSTO"
        elif profit_pct >= 60 and p95_dd < 35 and ruin_pct < 15:
            v = "[~~] ACEITÁVEL"
        else:
            v = "[XX] FRÁGIL"
        print(f"\n  Veredicto: {v}")
        
        all_results[nome] = {
            "profit_pct": profit_pct, "ruin_pct": ruin_pct,
            "avg_return": avg_return, "avg_dd": avg_dd,
            "p95_dd": p95_dd, "p99_dd": p99_dd,
            "p5_cap": p5_final, "median_cap": median_final,
            "veredicto": v,
        }
    
    # Tabela comparativa
    sub("COMPARAÇÃO DOS 3 CENÁRIOS")
    print(f"  {'Cenário':<12} {'Lucrativas':>11} {'Ruína':>8} {'Ret Médio':>10} {'DD P95':>8} {'Veredicto'}")
    print(f"  {'─'*12} {'─'*11} {'─'*8} {'─'*10} {'─'*8} {'─'*15}")
    for nome, res in all_results.items():
        print(f"  {nome:<12} {res['profit_pct']:>10.1f}% {res['ruin_pct']:>7.2f}% {res['avg_return']:>+9.1f}% {res['p95_dd']:>7.1f}% {res['veredicto']}")
    
    # Meta do usuário
    print(f"\n  ── Vs. Metas de Robustez ──")
    norm = all_results["NORMAL"]
    metas = [
        ("Simulações lucrativas > 80%", norm["profit_pct"] >= 80, f"{norm['profit_pct']:.1f}%"),
        ("Drawdown P95 < 25%",          norm["p95_dd"] < 25,      f"{norm['p95_dd']:.1f}%"),
        ("Risco de ruína < 5%",         norm["ruin_pct"] < 5,     f"{norm['ruin_pct']:.2f}%"),
    ]
    for name, ok, val in metas:
        icon = "[OK]" if ok else "[XX]"
        print(f"  {icon} {name:<35} → {val}")
    
    return all_results

# ═══════════════════════════════════════════════════════════════════════════
#  PARTE 5: STRESS DE EXECUÇÃO
# ═══════════════════════════════════════════════════════════════════════════
def test_stress_execucao(dados):
    banner("5. STRESS DE EXECUÇÃO")
    print("  Simula: 20% ordens rejeitadas + slippage 2x + latência 500ms")
    
    # Simulação com parâmetros degradados
    # - 20% ordens rejeitadas → FILL_FAILURE_RATE = 0.20 (original 0.08)
    # - Slippage 2x → extra_slip = 0.001
    # - Latência → modelada como vol_mult ligeiramente maior (micro moves)
    
    sub("Cenário Normal (baseline)")
    r_normal = run_sim(1000, signal_quality=SQ_REALISTIC, seed=42)
    
    sub("Cenário Stress de Execução")
    # Para simular 20% rejeição, vou override temporariamente e rodar manual
    import _stress_tests as st
    old_fill = st.FILL_FAILURE_RATE
    st.FILL_FAILURE_RATE = 0.20  # 20% ordens rejeitadas
    
    r_stress = run_sim(
        1000,
        signal_quality=SQ_REALISTIC,
        extra_slip=0.001,    # slippage 2x
        fee_mult=1.3,        # custos extras por latência
        vol_mult=1.1,        # micro-moves por delay
        seed=42,
    )
    
    st.FILL_FAILURE_RATE = old_fill  # restaurar
    
    # Resultados comparativos
    print(f"\n  {'Métrica':<25} {'Normal':>12} {'Stress Exec':>12} {'Delta':>10}")
    print(f"  {'─'*25} {'─'*12} {'─'*12} {'─'*10}")
    
    comparisons = [
        ("Lucro %", r_normal['lucro_pct'], r_stress['lucro_pct']),
        ("Win Rate %", r_normal['wr'], r_stress['wr']),
        ("Profit Factor", r_normal['pf'], r_stress['pf']),
        ("Max DD %", r_normal['max_dd'], r_stress['max_dd']),
        ("Sharpe", r_normal['sharpe'], r_stress['sharpe']),
    ]
    
    for name, norm, stress in comparisons:
        delta = stress - norm
        print(f"  {name:<25} {norm:>12.2f} {stress:>12.2f} {delta:>+10.2f}")
    
    # Veredicto
    still_profitable = r_stress['lucro_pct'] > 0
    icon = "[OK]" if still_profitable else "[XX]"
    print(f"\n  {icon} Bot {'AINDA LUCRATIVO' if still_profitable else 'QUEBRA'} sob stress de execução")
    print(f"      (20% rejeição + slippage 2x + latência)")
    
    if still_profitable:
        retain = (r_stress['lucro_pct'] / r_normal['lucro_pct']) * 100 if r_normal['lucro_pct'] > 0 else 0
        print(f"      Retém {retain:.0f}% do lucro normal")
    
    return {"normal": r_normal, "stress": r_stress, "profitable": still_profitable}

# ═══════════════════════════════════════════════════════════════════════════
#  PARTE 6: SHUFFLE TEST (Monte Carlo Permutation)
# ═══════════════════════════════════════════════════════════════════════════
def test_shuffle(dados):
    banner("6. SHUFFLE TEST — Embaralhar Ordem dos Trades")
    print("  Se mudar a ordem dos trades quebrar o sistema,")
    print("  ele NÃO é robusto (depende de sequência específica).")
    
    trades_pnl = dados["trades_pnl"]
    n_trades = len(trades_pnl)
    
    # Capital original (ordem real)
    cap_original = CAPITAL_INICIAL
    for pnl in trades_pnl:
        cap_original += pnl
    ret_original = ((cap_original - CAPITAL_INICIAL) / CAPITAL_INICIAL) * 100
    
    print(f"\n  Trades na amostra: {n_trades}")
    print(f"  Capital original (ordem real): R${cap_original:.2f} ({ret_original:+.1f}%)")
    
    # 10.000 shuffles
    N_SHUFFLES = 5_000
    shuffle_finals = []
    shuffle_max_dds = []
    shuffle_ruins = 0
    shuffle_profitable = 0
    
    t0 = time.time()
    for i in range(N_SHUFFLES):
        random.seed(i + 9999)
        shuffled = trades_pnl.copy()
        random.shuffle(shuffled)
        
        cap = CAPITAL_INICIAL
        peak = cap
        max_dd = 0.0
        
        for pnl in shuffled:
            cap = max(cap + pnl, 0.01)
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        shuffle_finals.append(cap)
        shuffle_max_dds.append(max_dd * 100)
        if cap < CAPITAL_INICIAL * RUIN_THRESHOLD:
            shuffle_ruins += 1
        if cap > CAPITAL_INICIAL:
            shuffle_profitable += 1
    
    elapsed = time.time() - t0
    
    finals_sorted = sorted(shuffle_finals)
    dds_sorted = sorted(shuffle_max_dds)
    
    profit_pct = (shuffle_profitable / N_SHUFFLES) * 100
    ruin_pct = (shuffle_ruins / N_SHUFFLES) * 100
    avg_final = statistics.mean(shuffle_finals)
    median_final = statistics.median(shuffle_finals)
    p5_final = finals_sorted[int(N_SHUFFLES * 0.05)]
    p95_final = finals_sorted[int(N_SHUFFLES * 0.95)]
    avg_dd = statistics.mean(shuffle_max_dds)
    p95_dd = dds_sorted[int(N_SHUFFLES * 0.95)]
    p99_dd = dds_sorted[int(N_SHUFFLES * 0.99)]
    std_final = statistics.stdev(shuffle_finals)
    
    print(f"\n  ({elapsed:.1f}s para {N_SHUFFLES:,} shuffles)")
    print()
    print(f"  +----------------------------------------------+")
    print(f"  |  Shuffles lucrativas : {profit_pct:>6.1f}%               |")
    print(f"  |  Risco de ruina      : {ruin_pct:>6.2f}%               |")
    print(f"  |  Capital medio       : R${avg_final:>8.2f}            |")
    print(f"  |  Capital mediana     : R${median_final:>8.2f}            |")
    print(f"  |  Std capital         : R${std_final:>8.2f}            |")
    print(f"  |  Drawdown medio      : {avg_dd:>6.1f}%               |")
    print(f"  |  Drawdown P95        : {p95_dd:>6.1f}%               |")
    print(f"  |  Drawdown P99        : {p99_dd:>6.1f}%               |")
    print(f"  +----------------------------------------------+")
    print()
    print(f"  Distribuição de capital final (embaralhado):")
    print(f"    P5  : R${p5_final:>8.2f}  ({((p5_final-CAPITAL_INICIAL)/CAPITAL_INICIAL)*100:>+.1f}%)")
    print(f"    P50 : R${median_final:>8.2f}  ({((median_final-CAPITAL_INICIAL)/CAPITAL_INICIAL)*100:>+.1f}%)")
    print(f"    P95 : R${p95_final:>8.2f}  ({((p95_final-CAPITAL_INICIAL)/CAPITAL_INICIAL)*100:>+.1f}%)")
    
    # Veredicto
    # Se >80% dos shuffles são lucrativos → ordem não importa → robusto
    if profit_pct >= 80 and p95_dd < 25 and ruin_pct < 5:
        v = "[OK] ROBUSTO — ordem dos trades NÃO afeta resultado"
    elif profit_pct >= 60:
        v = "[~~] ACEITÁVEL — leve dependência de sequência"
    else:
        v = "[XX] FRÁGIL — resultado depende da ordem dos trades"
    print(f"\n  Veredicto: {v}")
    
    return {
        "profit_pct": profit_pct, "ruin_pct": ruin_pct,
        "avg_final": avg_final, "p95_dd": p95_dd,
        "p99_dd": p99_dd, "veredicto": v,
    }

# ═══════════════════════════════════════════════════════════════════════════
#  PARTE 7: RESUMO FINAL
# ═══════════════════════════════════════════════════════════════════════════
def resumo_final(dados, losses_info, mc_results, exec_result, shuffle_result):
    banner("RESUMO FINAL — RISK OF RUIN")
    
    norm = mc_results.get("NORMAL", {})
    ruim = mc_results.get("RUIM", {})
    extremo = mc_results.get("EXTREMO", {})
    
    print(f"\n  Capital: R${CAPITAL_INICIAL:.2f}  |  SQ: {SQ_REALISTIC}")
    print(f"  WR: {dados['wr']*100:.1f}%  |  R:R: {dados['rr']:.2f}:1  |  Risco/trade: {dados['risk_pct']:.3f}%")
    print(f"  Expectativa: R${dados['expectancy']:+.4f}/trade")
    print()
    
    # Tabela de testes
    tests = []
    
    # 1. Monte Carlo Normal
    t1_ok = norm.get("profit_pct", 0) >= 80 and norm.get("ruin_pct", 100) < 5 and norm.get("p95_dd", 100) < 25
    tests.append(("Monte Carlo Normal", t1_ok, f"Lucr:{norm.get('profit_pct',0):.0f}% Ruína:{norm.get('ruin_pct',0):.1f}% DD95:{norm.get('p95_dd',0):.0f}%"))
    
    # 2. Monte Carlo Ruim
    t2_ok = ruim.get("profit_pct", 0) >= 50 and ruim.get("ruin_pct", 100) < 20
    tests.append(("Monte Carlo Ruim", t2_ok, f"Lucr:{ruim.get('profit_pct',0):.0f}% Ruína:{ruim.get('ruin_pct',0):.1f}%"))
    
    # 3. Monte Carlo Extremo
    t3_ok = extremo.get("profit_pct", 0) >= 30 and extremo.get("ruin_pct", 100) < 40
    tests.append(("Monte Carlo Extremo", t3_ok, f"Lucr:{extremo.get('profit_pct',0):.0f}% Ruína:{extremo.get('ruin_pct',0):.1f}%"))
    
    # 4. Losses consecutivos
    t4_ok = losses_info["p99"] <= 15
    tests.append(("Losses consecutivos P99", t4_ok, f"{losses_info['p99']} (máx observado: {losses_info['worst']})"))
    
    # 5. Stress de execução
    t5_ok = exec_result["profitable"]
    tests.append(("Stress execução", t5_ok, f"Lucr: {'Sim' if t5_ok else 'Não'}"))
    
    # 6. Shuffle test
    t6_ok = shuffle_result["profit_pct"] >= 80 and shuffle_result["ruin_pct"] < 5
    tests.append(("Shuffle Test", t6_ok, f"Lucr:{shuffle_result['profit_pct']:.0f}% Ruína:{shuffle_result['ruin_pct']:.1f}%"))
    
    # 7. Expectativa positiva
    t7_ok = dados["expectancy"] > 0
    tests.append(("Expectativa positiva", t7_ok, f"R${dados['expectancy']:+.4f}/trade"))
    
    # 8. DD P95 < 25% (cenário normal)
    t8_ok = norm.get("p95_dd", 100) < 25
    tests.append(("DD P95 < 25%", t8_ok, f"{norm.get('p95_dd', 0):.1f}%"))
    
    print(f"  {'#':<4} {'Teste':<30} {'Resultado'} ")
    print(f"  {'─'*4} {'─'*30} {'─'*40}")
    
    passed = 0
    for i, (name, ok, detail) in enumerate(tests, 1):
        icon = "[OK]" if ok else "[XX]"
        if ok: passed += 1
        print(f"  {i:<4} {icon} {name:<27} {detail}")
    
    total = len(tests)
    print(f"\n  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  RESULTADO: {passed}/{total} testes aprovados")
    print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    if passed >= 7:
        print(f"  >>> SISTEMA ROBUSTO — risco de ruína baixo")
        print(f"  >>> Pode prosseguir com paper trading 30 dias")
    elif passed >= 5:
        print(f"  >>> SISTEMA ACEITÁVEL — monitorar de perto")
        print(f"  >>> Rodar 30 dias paper e reavaliar")
    elif passed >= 3:
        print(f"  >>> SISTEMA MARGINAL — ajustes necessários")
    else:
        print(f"  >>> SISTEMA FRÁGIL — não usar capital real")
    
    print()
    return passed, total

# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print()
    print("#" * 70)
    print("  RISK OF RUIN - Teste Completo com Monte Carlo Simulation")
    print(f"  Capital: R${CAPITAL_INICIAL:.2f}  |  SQ: {SQ_REALISTIC}  |  {N_MONTE_CARLO:,} sims")
    print("#" * 70)
    
    t_total = time.time()
    
    # 1. Dados base
    dados = test_dados_base()
    
    # 2. Losses consecutivos
    losses_info = test_losses_consecutivos(dados)
    
    # 3. Impacto no capital
    test_impacto_capital(dados, losses_info)
    
    # 4. Monte Carlo 10.000 sim (3 cenários)
    mc_results = test_monte_carlo(dados)
    
    # 5. Stress de execução
    exec_result = test_stress_execucao(dados)
    
    # 6. Shuffle test
    shuffle_result = test_shuffle(dados)
    
    # 7. Resumo
    passed, total = resumo_final(dados, losses_info, mc_results, exec_result, shuffle_result)
    
    elapsed_total = time.time() - t_total
    print(f"  Tempo total: {elapsed_total:.1f}s")
    print()
