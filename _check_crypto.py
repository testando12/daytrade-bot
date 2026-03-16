import json, re
from collections import defaultdict

with open('data/trade_state.json') as f:
    ts = json.load(f)

with open('data/performance.json') as f:
    perf = json.load(f)

log = ts.get('log', [])

pnl_5m = pnl_1h = pnl_1d = pnl_vr = pnl_grid = 0.0
asset_types = defaultdict(int)

for entry in log:
    note = entry.get('note', '')
    etype = entry.get('type', '')

    if etype == 'CICLO':
        for pat, var in [('5m', ''), ('1h', ''), ('1d', ''), ('VR', ''), ('Grid', '')]:
            m = re.search(r'\b' + pat + r':\s*R\$([+-]?[\d.]+)', note)
            if m:
                val = float(m.group(1))
                if pat == '5m':   pnl_5m   += val
                elif pat == '1h': pnl_1h   += val
                elif pat == '1d': pnl_1d   += val
                elif pat == 'VR': pnl_vr   += val
                elif pat == 'Grid': pnl_grid += val

        if 'B3+Crypto' in note or 'Crypto+B3' in note:
            asset_types['B3+Crypto'] += 1
        elif 'Crypto' in note:
            asset_types['Crypto'] += 1
        elif 'B3' in note:
            asset_types['B3'] += 1
        elif 'AGRO' in note:
            asset_types['AGRO'] += 1

print("=== PNL POR ENGINE (log local) ===")
total = pnl_5m + pnl_1h + pnl_1d + pnl_vr + pnl_grid
print(f"  Momentum 5m : R${pnl_5m:+.2f}")
print(f"  Momentum 1h : R${pnl_1h:+.2f}")
print(f"  Momentum 1d : R${pnl_1d:+.2f}")
print(f"  VWAP Rev (VR): R${pnl_vr:+.2f}")
print(f"  Grid        : R${pnl_grid:+.2f}")
print(f"  TOTAL       : R${total:+.2f}")

print()
print("=== SESSOES ===")
for k, v in sorted(asset_types.items(), key=lambda x: -x[1]):
    print(f"  {k:15s}: {v} ciclos")

print()
print("=== PERFORMANCE ACUMULADA ===")
win = perf.get('win_count', 0)
loss = perf.get('loss_count', 0)
gain = perf.get('total_gain', 0)
loss_v = perf.get('total_loss', 0)
fees = perf.get('total_fees', 0)
print(f"  Wins: {win}  Loss: {loss}  WR: {win/(win+loss)*100:.1f}%")
print(f"  Total Gain : R${gain:.2f}")
print(f"  Total Loss : R${loss_v:.2f}")
print(f"  Total Fees : R${fees:.2f}")
print(f"  PnL Liquido: R${gain - loss_v:.2f}")
print(f"  PF (Profit Factor): {gain/loss_v:.2f}" if loss_v else "  PF: N/A")

print()
print("=== CUSTO DAS TAXAS ===")
print(f"  Brokerage  : R${perf.get('total_brokerage', 0):.2f}")
print(f"  Exchange   : R${perf.get('total_exchange_fees', 0):.2f}")
print(f"  Spread     : R${perf.get('total_spread', 0):.2f}")
print(f"  Slippage   : R${perf.get('total_slippage', 0):.2f}")
print(f"  FX         : R${perf.get('total_fx', 0):.2f}")

print()
print("=== ULTIMOS SINAIS DE TRADE ===")
for e in log[-30:]:
    t = e.get('type', '')
    if t in ['VR_SIGNAL', 'BO_SIGNAL', 'LS_SIGNAL', 'ENTRY', 'EXIT', 'TAKE_PROFIT_ATR', 'STOP_LOSS_ATR', 'PARTIAL_TP']:
        asset = e.get('asset', '-')
        amt = e.get('amount', 0)
        note = e.get('note', '')[:100]
        print(f"  {t:20s} {asset:12s} amt={amt:.2f}  {note}")
