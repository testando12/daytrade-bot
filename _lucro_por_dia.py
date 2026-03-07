import requests
from datetime import date

url = "http://localhost:8000"
d = requests.get(f"{url}/performance", timeout=20).json()["data"]
cambio = 5.80

today        = date.today().strftime("%d/%m/%Y")
pnl_today    = d.get("pnl_today", 0)
pnl_5m       = d.get("pnl_today_5m", 0)
pnl_1h       = d.get("pnl_today_1h", 0)
pnl_1d       = d.get("pnl_today_1d", 0)
n            = d.get("today_cycles", 0)
gain         = d.get("today_gain", 0)
loss         = d.get("today_loss", 0)
costs        = d.get("costs_today_total", 0)
capital      = d.get("current_capital", 0)
pnl_all      = d.get("total_pnl", 0)
total_cycles = d.get("total_cycles", 0)
sharpe       = d.get("sharpe_ratio", 0)
wr           = d.get("win_rate_pct", 0)

retorno_pct = 100 * pnl_today / capital if capital > 0 else 0

W = 60
sep = "=" * W

print(sep)
print(f"  RESULTADO DO DIA  {today}".center(W))
print(sep)
print(f"  Lucro liquido hoje   : R  (~US)")
print(f"  Retorno % hoje       : {retorno_pct:+.3f}%")
print(f"  Ciclos hoje          : {n}")
print(f"  Ganhos brutos        : R")
print(f"  Perdas brutas        : -R")
print(f"  Custos (fees)        : -R")
print()
print(f"  Breakdown timeframe  :")
print(f"    5m                 : R")
print(f"    1h                 : R")
print(f"    1d                 : R")
print()
print(f"  Capital atual        : R")
print(sep)
print(f"  ACUMULADO TOTAL      : R  (~US)")
print(f"  Total ciclos         : {total_cycles}  |  Win rate: {wr:.1f}%  |  Sharpe: {sharpe:.2f}")
print(sep)
