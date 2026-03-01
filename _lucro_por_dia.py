import httpx
from collections import defaultdict

url = "https://daytrade-bot.onrender.com"
r = httpx.get(f"{url}/performance", timeout=30).json()
data = r.get("data", {})

cycles = data.get("recent_cycles", [])
total_cycles = data.get("total_cycles", 0)
total_pnl = data.get("total_pnl", 0)
win_rate = data.get("win_rate_pct", 0)
best_cycle = data.get("best_cycle_pnl", 0)
worst_cycle = data.get("worst_cycle_pnl", 0)

print("=" * 55)
print("  RESUMO GERAL DO BOT")
print("=" * 55)
print(f"  Total ciclos:       {total_cycles}")
print(f"  Win rate:           {win_rate:.1f}%")
print(f"  Melhor ciclo:       R${best_cycle:.2f}")
print(f"  Pior ciclo:         R${worst_cycle:.2f}")
print(f"  PnL TOTAL:          R${total_pnl:.2f}")
print("=" * 55)
print()

# Agrupar ciclos recentes por dia
by_day = defaultdict(lambda: {"pnl": 0, "pnl_5m": 0, "pnl_1h": 0, "pnl_1d": 0,
                               "cycles": 0, "wins": 0, "losses": 0, "best": 0, "worst": 0})

for c in cycles:
    ts = c.get("timestamp", "")
    if not ts:
        continue
    day = ts[:10]  # YYYY-MM-DD
    pnl = c.get("pnl", 0) or 0
    by_day[day]["pnl"]    += pnl
    by_day[day]["pnl_5m"] += c.get("pnl_5m", 0) or 0
    by_day[day]["pnl_1h"] += c.get("pnl_1h", 0) or 0
    by_day[day]["pnl_1d"] += c.get("pnl_1d", 0) or 0
    by_day[day]["cycles"] += 1
    if pnl > 0:
        by_day[day]["wins"] += 1
        if pnl > by_day[day]["best"]:
            by_day[day]["best"] = pnl
    elif pnl < 0:
        by_day[day]["losses"] += 1
        if pnl < by_day[day]["worst"]:
            by_day[day]["worst"] = pnl

cambio = 5.80

print("=== PnL por DIA (ultimos ciclos registrados) ===")
print()
for day in sorted(by_day.keys()):
    d = by_day[day]
    sinal = "LUCRO" if d["pnl"] >= 0 else "PERDA"
    usd = d["pnl"] / cambio
    print(f"  {day}  [{sinal}]")
    print(f"    Total:   R${d['pnl']:+.2f}  (~US${usd:+.2f})")
    print(f"    5m:      R${d['pnl_5m']:+.2f}  |  1h: R${d['pnl_1h']:+.2f}  |  1d: R${d['pnl_1d']:+.2f}")
    print(f"    Ciclos:  {d['cycles']}  |  {d['wins']}W / {d['losses']}L  |  melhor: R${d['best']:.2f}  pior: R${d['worst']:.2f}")
    print()

if len(cycles) < total_cycles:
    diff = total_cycles - len(cycles)
    print(f"  (Aviso: API retornou {len(cycles)} ciclos recentes de {total_cycles} totais.)")
    print(f"  ({diff} ciclos anteriores nao aparecem no detalhe por dia, mas estao no total R${total_pnl:.2f})")
