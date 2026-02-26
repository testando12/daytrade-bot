"""Simulação REAL com dados de mercado ao vivo — 10 ciclos rápidos com R$ 2.000"""
import requests
import json
import time

BASE = "http://localhost:8001"

print("=" * 65)
print("  SIMULAÇÃO REAL — DADOS AO VIVO — 14 ESTRATÉGIAS — R$ 2.000")
print("=" * 65)

# 1. Resetar estado para começar limpo com R$2000
print("\n[1] Resetando estado do bot...")
try:
    r = requests.post(f"{BASE}/trade/reset", json={"capital": 2000}, timeout=10)
    print(f"    Reset: {r.json().get('message', 'OK')}")
except Exception as e:
    print(f"    Aviso reset: {e}")

# 2. Verificar estratégias
print("\n[2] Estratégias ativas:")
try:
    sr = requests.get(f"{BASE}/trade/strategies", timeout=10)
    strats = sr.json().get("data", {})
    active = [k for k, v in strats.items() if isinstance(v, dict) and v.get("active")]
    for s in active:
        print(f"    ✓ {s}")
    print(f"    Total: {len(active)} estratégias")
except Exception as e:
    print(f"    Erro: {e}")

# 3. Rodar 10 ciclos reais
print(f"\n[3] Executando 10 ciclos reais com dados do mercado...")
print("-" * 65)

results = []
for i in range(10):
    try:
        resp = requests.post(f"{BASE}/trade/cycle", timeout=60)
        d = resp.json().get("data", {})
        
        cpnl = d.get("cycle_pnl", 0)
        grid = d.get("grid_pnl", 0)
        turbo = d.get("turbo_active", False)
        source = d.get("data_source", "?")
        assets = d.get("assets_analyzed", 0)
        prot = d.get("protection", {})
        
        pnl_5m = d.get("pnl_5m", 0)
        pnl_1h = d.get("pnl_1h", 0)
        pnl_1d = d.get("pnl_1d", 0)
        
        strats_info = d.get("strategies_active", {})
        turbo_str = " 🚀TURBO" if turbo else ""
        grid_str = f" Grid:+R${grid:.2f}" if grid > 0 else ""
        
        total_acc = sum(r["pnl"] for r in results) + cpnl
        
        print(f"  Ciclo {i+1:2}/10 | P&L: R$ {cpnl:+8.4f} (5m:{pnl_5m:+.2f} 1h:{pnl_1h:+.2f} 1d:{pnl_1d:+.2f}){grid_str}{turbo_str} | {source} | {assets} ativos | Acum: R$ {total_acc:+.2f}")
        
        results.append({
            "cycle": i + 1,
            "pnl": cpnl,
            "grid": grid,
            "turbo": turbo,
            "source": source,
            "pnl_5m": pnl_5m,
            "pnl_1h": pnl_1h,
            "pnl_1d": pnl_1d,
        })
        
        # Pequena pausa entre ciclos
        if i < 9:
            time.sleep(2)
    
    except Exception as e:
        print(f"  Ciclo {i+1:2}/10 | ERRO: {e}")

# 4. Resultado final
print(f"\n{'=' * 65}")
print(f"  RESULTADO DA SIMULAÇÃO REAL (10 ciclos)")
print(f"{'=' * 65}")

if results:
    total_pnl = sum(r["pnl"] for r in results)
    grid_total = sum(r["grid"] for r in results)
    turbo_count = sum(1 for r in results if r["turbo"])
    wins = sum(1 for r in results if r["pnl"] > 0)
    losses = sum(1 for r in results if r["pnl"] < 0)
    zeros = sum(1 for r in results if r["pnl"] == 0)
    best = max(r["pnl"] for r in results)
    worst = min(r["pnl"] for r in results)
    
    final_cap = 2000 + total_pnl
    
    print(f"\n  Capital Inicial:     R$ 2.000,00")
    print(f"  Capital Final:       R$ {final_cap:,.2f}")
    print(f"  P&L Total:           R$ {total_pnl:+,.4f}")
    print(f"  Grid Trading Total:  R$ {grid_total:+,.4f}")
    print(f"  Retorno:             {(total_pnl/2000*100):+.3f}%")
    print(f"  Win/Loss/Zero:       {wins}W / {losses}L / {zeros}Z")
    wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    print(f"  Win Rate:            {wr:.1f}%")
    print(f"  Melhor ciclo:        R$ {best:+.4f}")
    print(f"  Pior ciclo:          R$ {worst:+.4f}")
    print(f"  Turbo ativado:       {turbo_count} de {len(results)} ciclos")
    
    # Fontes de dados
    sources = {}
    for r in results:
        s = r["source"]
        sources[s] = sources.get(s, 0) + 1
    print(f"  Fontes de dados:     {sources}")
    
    # Projeção
    avg = total_pnl / len(results)
    daily_proj = avg * 72  # ~72 ciclos/dia
    monthly_proj = daily_proj * 22
    
    print(f"\n  --- PROJEÇÃO ---")
    print(f"  P&L médio/ciclo:     R$ {avg:+.4f}")
    print(f"  Projeção diária:     R$ {daily_proj:+.2f}")
    print(f"  Projeção mensal:     R$ {monthly_proj:+.2f}")
    print(f"  Retorno mensal:      {(monthly_proj/2000*100):+.1f}%")

# 5. Checar performance acumulada
print(f"\n{'=' * 65}")
print(f"  PERFORMANCE ACUMULADA NO SERVIDOR")
print(f"{'=' * 65}")
try:
    pr = requests.get(f"{BASE}/performance", timeout=10)
    pd = pr.json().get("data", {})
    print(f"  Total ciclos:        {pd.get('total_cycles', 0)}")
    print(f"  P&L Total:           R$ {pd.get('total_pnl', 0):+.2f}")
    print(f"  Win Rate:            {pd.get('win_rate_pct', 0):.1f}%")
    print(f"  Capital atual:       R$ {pd.get('current_capital', 0):,.2f}")
    print(f"  Sharpe Ratio:        {pd.get('sharpe_ratio', 0):.4f}")
    print(f"  Max Drawdown:        {pd.get('max_drawdown_pct', 0):.2f}%")
    print(f"  Hoje - Ganho:        R$ {pd.get('today_gain', 0):+.2f}")
    print(f"  Hoje - Perda:        R$ {pd.get('today_loss', 0):.2f}")
except Exception as e:
    print(f"  Erro: {e}")

print(f"\n{'=' * 65}")
print(f"  SIMULAÇÃO CONCLUÍDA")
print(f"{'=' * 65}")
