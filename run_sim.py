"""Simulação completa com todas as 14 estratégias — R$ 2.000"""
import requests
import json
import sys

BASE = "http://localhost:8001"

print("=" * 60)
print("  SIMULAÇÃO — 14 ESTRATÉGIAS ATIVAS — R$ 2.000")
print("=" * 60)

# 1. Verificar estratégias ativas
try:
    sr = requests.get(f"{BASE}/trade/strategies", timeout=10)
    if sr.status_code == 200:
        strats = sr.json().get("data", {})
        active = [k for k, v in strats.items() if isinstance(v, dict) and v.get("active")]
        print(f"\nEstratégias ativas: {len(active)}")
        for s in active:
            print(f"  ✓ {s}")
    else:
        print("Aviso: /trade/strategies retornou", sr.status_code)
except Exception as e:
    print(f"Aviso: não foi possível verificar estratégias: {e}")

# 2. Rodar simulação com 50 ciclos
print(f"\n{'=' * 60}")
print(f"  Executando 50 ciclos...")
print(f"{'=' * 60}\n")

try:
    resp = requests.post(
        f"{BASE}/simulate",
        json={"capital": 2000, "cycles": 50, "interval": "5m", "limit": 100},
        timeout=300,
    )
    data = resp.json()
except Exception as e:
    print(f"ERRO na simulação: {e}")
    sys.exit(1)

if not data.get("success"):
    print("ERRO:", json.dumps(data, indent=2))
    sys.exit(1)

d = data["data"]
final_cap = d.get("final_capital", 2000)
total_pnl = d.get("total_pnl", 0)
retorno = ((final_cap - 2000) / 2000) * 100

print(f"  Capital Inicial:     R$ 2.000,00")
print(f"  Capital Final:       R$ {final_cap:,.2f}")
print(f"  P&L Total:           R$ {total_pnl:+,.2f}")
print(f"  Retorno:             {retorno:+.2f}%")
print(f"  Ciclos executados:   {d.get('total_cycles', 0)}")
print(f"  Win Rate:            {d.get('win_rate_pct', 0):.1f}%")
print(f"  Wins:                {d.get('wins', 0)}")
print(f"  Losses:              {d.get('losses', 0)}")
print(f"  Melhor ciclo:        R$ {d.get('best_cycle_pnl', 0):+.4f}")
print(f"  Pior ciclo:          R$ {d.get('worst_cycle_pnl', 0):+.4f}")
print(f"  P&L médio/ciclo:     R$ {d.get('avg_pnl_per_cycle', 0):+.4f}")
print(f"  Max Drawdown:        {d.get('max_drawdown_pct', 0):.2f}%")
print(f"  Sharpe Ratio:        {d.get('sharpe_ratio', 0):.4f}")

# Top 10 ativos
stats = d.get("asset_stats", {})
if stats:
    sorted_assets = sorted(stats.items(), key=lambda x: x[1].get("total_pnl", 0), reverse=True)
    print(f"\n{'=' * 60}")
    print(f"  TOP 10 ATIVOS")
    print(f"{'=' * 60}")
    for i, (asset, s) in enumerate(sorted_assets[:10], 1):
        w = s.get("wins", 0)
        l = s.get("losses", 0)
        total = w + l
        wr = (w / total * 100) if total > 0 else 0
        pnl = s.get("total_pnl", 0)
        print(f"  {i:2}. {asset:8s} | P&L: R$ {pnl:+8.4f} | WR: {wr:5.1f}% ({w}W/{l}L)")

    # Bottom 5 (piores)
    worst = sorted_assets[-5:]
    worst.reverse()
    print(f"\n  BOTTOM 5 (piores):")
    for i, (asset, s) in enumerate(worst, 1):
        pnl = s.get("total_pnl", 0)
        print(f"  {i:2}. {asset:8s} | P&L: R$ {pnl:+8.4f}")

# Equity curve
eq = d.get("equity_curve", [])
if eq:
    print(f"\n{'=' * 60}")
    print(f"  EQUITY CURVE")
    print(f"{'=' * 60}")
    step = max(1, len(eq) // 10)
    for i in range(0, len(eq), step):
        bar_len = max(0, int((eq[i] - 1990) / 2))
        bar = "█" * min(bar_len, 50)
        print(f"  Ciclo {i:3}: R$ {eq[i]:>10,.2f} {bar}")
    if len(eq) - 1 not in range(0, len(eq), step):
        bar_len = max(0, int((eq[-1] - 1990) / 2))
        bar = "█" * min(bar_len, 50)
        print(f"  Ciclo {len(eq)-1:3}: R$ {eq[-1]:>10,.2f} {bar}")

# Projeção diária/mensal
if total_pnl > 0 and d.get("total_cycles", 0) > 0:
    avg_per_cycle = total_pnl / d["total_cycles"]
    # Assumindo 6 ciclos/hora × 12h = ~72 ciclos/dia
    daily_proj = avg_per_cycle * 72
    monthly_proj = daily_proj * 22  # 22 dias úteis
    print(f"\n{'=' * 60}")
    print(f"  PROJEÇÃO (baseada na simulação)")
    print(f"{'=' * 60}")
    print(f"  P&L médio/ciclo:     R$ {avg_per_cycle:+.4f}")
    print(f"  Projeção diária:     R$ {daily_proj:+.2f}  (~72 ciclos/dia)")
    print(f"  Projeção mensal:     R$ {monthly_proj:+.2f}  (22 dias)")
    print(f"  Retorno mensal:      {(monthly_proj/2000*100):+.1f}%")

print(f"\n{'=' * 60}")
print(f"  SIMULAÇÃO CONCLUÍDA")
print(f"{'=' * 60}")
