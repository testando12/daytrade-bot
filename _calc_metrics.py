"""Calcula as 4 metricas reais do bot em producao."""
import requests

r = requests.get('http://localhost:8000/performance', timeout=15)
d = r.json()['data']

cycles = d['recent_cycles']
pnls = [c['pnl'] for c in cycles]
wins = [p for p in pnls if p > 0]
losses = [abs(p) for p in pnls if p <= 0]
capital = d['current_capital']

print('=' * 55)
print('  METRICAS REAIS DO BOT EM PRODUCAO')
print('=' * 55)
print(f"  Ciclos totais   : {d['total_cycles']}")
print(f"  Capital atual   : R${capital:.2f}")
print()

# 1. Win Rate
print("  1. WIN RATE REAL")
print(f"     Wins: {d['win_count']}  |  Losses: {d['loss_count']}")
print(f"     Win Rate: {d['win_rate_pct']:.2f}%")
print()

# 2. Media de ganho
avg_gain = d['today_gain'] / d['win_count'] if d['win_count'] else 0
print("  2. MEDIA DE GANHO")
print(f"     Ganho total  : R${d['today_gain']:.2f} em {d['win_count']} wins")
print(f"     Media/win    : R${avg_gain:.2f}")
print()

# 3. Media de perda
avg_loss = d['today_loss'] / d['loss_count'] if d['loss_count'] else 0
print("  3. MEDIA DE PERDA")
print(f"     Perda total  : R${d['today_loss']:.2f} em {d['loss_count']} losses")
print(f"     Media/loss   : R${avg_loss:.2f}")
print()

# 4. Risco por trade
risk_pct = (avg_loss / capital) * 100
print("  4. RISCO POR TRADE (% do capital)")
print(f"     Risco medio  : R${avg_loss:.2f} / R${capital:.2f}")
print(f"     % do capital : {risk_pct:.3f}%")
print()

# Metricas derivadas
rr = avg_gain / avg_loss if avg_loss > 0 else 0
pf = d['today_gain'] / d['today_loss'] if d['today_loss'] > 0 else 999
wr = d['win_rate_pct'] / 100
expectancy = (wr * avg_gain) - ((1 - wr) * avg_loss)

print("  --- METRICAS DERIVADAS ---")
print(f"     R:R Ratio    : {rr:.2f}:1")
print(f"     Profit Factor: {pf:.2f}")
print(f"     Expectativa  : R${expectancy:+.2f}/ciclo")
print(f"     Custos totais: R${d['costs_today_total']:.2f}")
print(f"     Sharpe       : {d['sharpe_ratio']:.2f}")
print()

# Detalhamento dos ultimos ciclos
print("  --- ULTIMOS CICLOS (PnL) ---")
for i, c in enumerate(cycles[-20:], 1):
    ts = c['timestamp'][11:19]
    icon = "[+]" if c['pnl'] > 0 else "[-]"
    print(f"    {icon} {ts}  PnL: R${c['pnl']:>+8.2f}  Fees: R${c['fees_total']:.2f}")

print()
print(f"  AVISO: Apenas {d['total_cycles']} ciclos rodados ate agora.")
print(f"  Precisa de 300+ ciclos para significancia estatistica.")
print(f"  Tempo estimado: ~2 dias de operacao continua (10min/ciclo).")
print()
