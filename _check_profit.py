import urllib.request, json

BASE = "http://localhost:8000"

r = urllib.request.urlopen(BASE + "/trade/status", timeout=15)
trade = json.loads(r.read().decode()).get("data", {})

r2 = urllib.request.urlopen(BASE + "/performance", timeout=15)
perf = json.loads(r2.read().decode()).get("data", {})

r3 = urllib.request.urlopen(BASE + "/scheduler/status", timeout=15)
sched = json.loads(r3.read().decode()).get("data", {})

cap = trade.get("capital", 0)
pnl_today = trade.get("pnl_today", 0)
total_pnl = trade.get("total_pnl", 0)
mode = trade.get("trading_mode", "?")
positions = trade.get("positions", {})
cycles = sched.get("total_auto_cycles", 0)
wins = perf.get("win_count", 0)
losses = perf.get("loss_count", 0)
total_fees = perf.get("total_fees", 0)
best = perf.get("best_day_pnl", 0)
worst = perf.get("worst_day_pnl", 0)
total_gain = perf.get("total_gain", 0)
total_loss = perf.get("total_loss", 0)

D = "$"
print(f"Capital: R{D}{cap:.2f}")
print(f"Lucro hoje: R{D}{pnl_today:+.2f}")
print(f"Lucro total: R{D}{total_pnl:+.2f}")
print(f"Ganho acum: R{D}{total_gain:+.2f} | Perda acum: R{D}{total_loss:.2f}")
print(f"Taxas pagas: R{D}{abs(total_fees):.2f}")
print(f"Ciclos: {cycles} | Wins: {wins} | Losses: {losses}")
wr = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0
print(f"Win rate: {wr}%")
print(f"Melhor ciclo: R{D}{best:+.4f} | Pior: R{D}{worst:+.4f}")
print(f"Modo: {mode}")
print(f"Posições abertas: {len(positions)}")
for a, info in list(positions.items())[:5]:
    amt = info.get("amount", 0)
    tf = info.get("tf", "?")
    print(f"  {a}: R{D}{amt:.2f} ({tf})")
