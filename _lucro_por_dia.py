import httpx

url = "https://daytrade-bot.onrender.com"
r = httpx.get(f"{url}/performance/history", timeout=30).json()

if not r.get("success"):
    # Fallback para /performance se o novo endpoint ainda nao deployou
    r2 = httpx.get(f"{url}/performance", timeout=30).json()
    data = r2.get("data", {})
    print("Endpoint novo ainda carregando. Dados parciais via /performance:")
    print(f"Total acumulado: R${data.get('total_pnl', 0):.2f} em {data.get('total_cycles', 0)} ciclos")
    print(f"Win rate: {data.get('win_rate_pct', 0):.1f}%")
    print(f"Hoje: R${data.get('pnl_today', 0):.2f}")
    print(f"Melhor ciclo: R${data.get('best_cycle_pnl', 0):.2f}")
    print(f"Pior ciclo: R${data.get('worst_cycle_pnl', 0):.2f}")
    import sys; sys.exit(0)

cambio = 5.80
total_pnl = r["total_pnl"]
total_cycles = r["total_cycles"]
days = r.get("days", [])

print("=" * 60)
print("  LUCRO POR DIA - HISTORICO COMPLETO")
print("=" * 60)
print(f"  TOTAL ACUMULADO: R${total_pnl:.2f}  (~US${total_pnl/cambio:.2f})  |  {total_cycles} ciclos")
print("=" * 60)
print()

if not days:
    print("  Sem dados historicos ainda.")
else:
    for d in days:
        sinal = "LUCRO" if d["pnl"] >= 0 else "PERDA"
        usd = d["pnl"] / cambio
        wr = d["win_rate_pct"]
        print(f"  {d['date']}  [{sinal}]  R${d['pnl']:+.2f}  (~US${usd:+.2f})")
        print(f"    Ciclos: {d['cycles']}  |  {d['wins']}W/{d['losses']}L  ({wr:.0f}% win rate)")
        print(f"    5m: R${d['pnl_5m']:.2f}  |  1h: R${d['pnl_1h']:.2f}  |  1d: R${d['pnl_1d']:.2f}")
        print(f"    Melhor ciclo: R${d['best_cycle']:.2f}  |  Pior ciclo: R${d['worst_cycle']:.2f}")
        print()

