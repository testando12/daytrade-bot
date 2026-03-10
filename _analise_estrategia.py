"""
Analise de desempenho por estrategia — Railway
"""
import urllib.request, json, re
from collections import defaultdict

key = '4OsimFow5MmJ6Ttx5Bux9Fd6yCu8okM_djQodNjNmOc'
base = 'https://daytrade-bot-production.up.railway.app'

r = urllib.request.urlopen(urllib.request.Request(
    base + '/trade/status', headers={'X-API-Key': key}), timeout=15)
data = json.loads(r.read()).get('data', {})
log  = data.get('log', [])

ciclos   = [e for e in log if e.get('type') == 'CICLO']
entradas = [e for e in log if e.get('type') == 'ENTRY']
saidas   = [e for e in log if e.get('type') in ('EXIT','STOP_LOSS_ATR','PARTIAL_TP')]

pnl_fonte = defaultdict(float)
cnt_fonte = defaultdict(int)
for c in ciclos:
    note = c.get('note', '')
    for match in re.finditer(r'(5m|1h|1d|BO|VR|LS|Grid):\s*R\$([+-][0-9.]+)', note):
        src, val = match.group(1), float(match.group(2))
        pnl_fonte[src] += val
        if val != 0:
            cnt_fonte[src] += 1

print("=" * 55)
print(f"  ANALISE DE DESEMPENHO — capital R${data.get('capital',0):.2f}")
print("=" * 55)

print("\n[ Contribuicao por estrategia (log atual) ]")
total_geral = 0.0
for src in ['5m','1h','1d','BO','VR','LS','Grid']:
    v = pnl_fonte[src]
    total_geral += v
    bar = '▲' if v > 0 else ('▼' if v < 0 else '─')
    print(f"  {src:>5}: {bar} {v:+7.4f}  ({cnt_fonte[src]} ciclos ativos)")

print(f"  {'TOTAL':>5}:   {total_geral:+7.4f}")

# Ativos mais operados
ativos = defaultdict(lambda: {'entrada': 0, 'saida': 0})
for e in entradas:
    ativos[e.get('asset','?')]['entrada'] += 1
for e in saidas:
    ativos[e.get('asset','?')]['saida'] += 1

print("\n[ Top 5 ativos mais operados ]")
ranking = sorted(ativos.items(), key=lambda x: x[1]['entrada']+x[1]['saida'], reverse=True)[:5]
for ativo, ct in ranking:
    print(f"  {ativo:>8}: {ct['entrada']} entradas  {ct['saida']} saidas")

# Padrão de stop loss
stops = [e for e in log if e.get('type') == 'STOP_LOSS_ATR']
partial = [e for e in log if e.get('type') == 'PARTIAL_TP']
print(f"\n[ Saidas ]")
print(f"  Stop loss ATR:    {len(stops)}")
print(f"  Partial Take Profit: {len(partial)}")
print(f"  Saidas normais:   {len([e for e in log if e.get('type') == 'EXIT'])}")

# Reducoes de tamanho (sinal ruim)
reducoes = [e for e in log if e.get('type') in ('LOSS_REDUCE','REDUCED','RECOVERY')]
print(f"\n[ Controle de risco ]")
print(f"  Reducoes por perdas seguidas: {len([e for e in reducoes if e.get('type')=='LOSS_REDUCE'])}")
print(f"  Recuperacoes (volta 100%):    {len([e for e in reducoes if e.get('type')=='RECOVERY'])}")

print("\n[ Diagnostico ]")
capital = data.get('capital', 450)
alloc = data.get('strategy', {}).get('alloc_effective_pct', {})
pos_1h = capital * alloc.get('1h', 35) / 100
pos_5m = capital * alloc.get('5m', 15) / 100
print(f"  Tamanho tipico posicao 1h:  R${pos_1h:.2f}  ({alloc.get('1h',35):.0f}% de R${capital:.0f})")
print(f"  Tamanho tipico posicao 5m:  R${pos_5m:.2f}  ({alloc.get('5m',15):.0f}% de R${capital:.0f})")
print(f"  VR sinal tipico:            R${capital*0.11:.2f}  (11% do capital)")
print(f"  LS sinal tipico:            R${capital*0.022:.2f}  (2.2% do capital)")
print()

# Conclusao
print("[ Conclusao ]")
if total_geral < 0:
    print("  ⚠  TOTAL NEGATIVO no log atual — estrategia perdendo")
elif abs(total_geral) < 5:
    print("  ⚠  TOTAL PROXIMO DE ZERO — ganhos e perdas se cancelam")
else:
    print(f"  ✅  TOTAL POSITIVO +{total_geral:.2f}")

perdas_ls = pnl_fonte.get('LS', 0)
perdas_5m = pnl_fonte.get('5m', 0)
if perdas_ls < -1:
    print(f"  ⚠  LiquiditySweep NEGATIVO ({perdas_ls:.4f}) — considere desativar")
if perdas_5m < -1:
    print(f"  ⚠  Momentum 5m NEGATIVO ({perdas_5m:.4f}) — muito ruido p/ capital baixo")
if capital < 600:
    print(f"  ⚠  Capital R${capital:.0f} — some estrategias tem tamanho minimo ineficiente")
    print(f"     Considere focar apenas nos sinais com melhor Edge neste capital")
