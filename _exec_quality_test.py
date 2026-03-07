"""
EXECUTION QUALITY STRESS TEST v2
Usa os 225 ciclos reais (statísticas agregadas) para responder:
  O edge do bot sobrevive a +0.05% taxa, +0.10% slippage e 100ms latencia?

Formula do servidor (main.py linha 3447):
  sharpe = (mean(pnls) / stdev(pnls)) * sqrt(252)

Custo extra e calculado a partir do notional medio derivado dos custos reais.
"""
import requests, math

API = "http://localhost:8000"
print("Buscando dados locais...")
r = requests.get(f"{API}/performance", timeout=20)
d = r.json()["data"]

N           = int(d["total_cycles"])
win_count   = int(d["win_count"])
loss_count  = int(d["loss_count"])
total_gain  = float(d["total_gain"])
total_loss  = float(d["total_loss"])
avg_pnl     = float(d["avg_pnl_per_cycle"])
sharpe_srv  = float(d["sharpe_ratio"])
capital     = float(d["current_capital"])
avg_cost    = float(d["costs_total"]) / N
cost_brok   = float(d["costs_total_brokerage"])
wr          = win_count / (win_count + loss_count) if (win_count + loss_count) > 0 else 0

avg_win  = total_gain / win_count  if win_count  else 0
avg_loss = total_loss / loss_count if loss_count else 0

# sigma derivado da formula do servidor: sharpe = (mu/sigma)*sqrt(252)
sigma = (avg_pnl * math.sqrt(252)) / sharpe_srv if sharpe_srv else 1.0

# Notional medio por ciclo (via brokerage: b3 cobra 2bps*2 = 4bps rt)
avg_brok   = cost_brok / N
notional   = avg_brok / 0.0004   # R$/ciclo

print(f"OK  {N} ciclos | WR {round(wr*100,1)}% | avg_pnl R${avg_pnl} | Sharpe {sharpe_srv}")
print(f"    avg_win=R${round(avg_win,2)} | avg_loss=R${round(avg_loss,2)} | sigma=R${round(sigma,4)}")
print(f"    notional estimado = R${round(notional,0)}/ciclo | avg_cost = R${round(avg_cost,4)}/ciclo")
print()

# Custo extra por cenario (one-way quando aplicavel, round-trip para taxa)
# Modelo atual: brokerage x2 (rt), slippage one-way, spread one-way
e_taxa    = notional * (5+5) / 10000   # +5bps*2 rt = +10bps total
e_slip    = notional * 10   / 10000   # +10bps slippage one-way
e_latency = notional *  5   / 10000   # 100ms ~ +5bps adverse entry

def sharpe_adj(extra):
    mu_new = avg_pnl - extra
    return round((mu_new / sigma) * math.sqrt(252), 2) if sigma > 0 else 0.0

def pf_adj(extra):
    g = total_gain - extra * win_count
    l = total_loss + extra * loss_count
    return round(g / max(l, 0.01), 3)

scenarios = [
    ("BASELINE",           0.0),
    ("+Taxa  (+0.05%)",    e_taxa),
    ("+Slip  (+0.10%)",    e_slip),
    ("+Taxa+Slip",         e_taxa + e_slip),
    ("+TUDO (taxa+slip+lat)", e_taxa + e_slip + e_latency),
]

print("=" * 68)
print("  EXECUTION QUALITY STRESS TEST  --  225 ciclos reais")
print("=" * 68)
print(f"  {'Cenario':<28} {'Extra/ciclo':>12}  {'Sharpe':>7}  {'PF':>7}  {'mu/ciclo':>10}")
print(f"  {'─'*28} {'─'*12}  {'─'*7}  {'─'*7}  {'─'*10}")

results = []
for name, extra in scenarios:
    sh = sharpe_adj(extra)
    pf = pf_adj(extra)
    mu = round(avg_pnl - extra, 4)
    tag = " [OK]" if sh >= 2.0 else (" [~~]" if sh >= 1.0 else " [XX]")
    print(f"  {name:<28} R${extra:>9.4f}  {sh:>7.2f}  {pf:>7.3f}  R${mu:>8.4f}{tag}")
    results.append((name, extra, sh, pf, mu))

# Break-even
be_cost = avg_pnl
be_bps  = (be_cost / notional) * 10000 if notional > 0 else 0

print()
print("  BREAK-EVEN:")
print(f"  Edge some com extra  >= R${round(be_cost,4)}/ciclo = {round(be_bps,1)} bps sobre notional")
print(f"  Custo atual total    =  R${round(avg_cost,4)}/ciclo")
print(f"  Margem para degradar =  R${round(be_cost - results[-1][1],4)}/ciclo (apos stress total)")
print()

sh_worst = results[-1][2]
pf_worst = results[-1][3]
mu_worst = results[-1][4]

print("=" * 68)
print("  VEREDICTO")
print("=" * 68)
print(f"  Sharpe: {sharpe_srv}  -->  {sh_worst}  (queda de {round((sharpe_srv - sh_worst)/sharpe_srv*100,1)}%)")
print(f"  PF    : {round(total_gain/max(total_loss,0.01),3)}  -->  {pf_worst}")
print(f"  mu    : R${avg_pnl}/ciclo  -->  R${mu_worst}/ciclo")
print()
if sh_worst >= 2.0:
    print("  [OK] EDGE REAL")
    print("       Sharpe permanece >2 mesmo com taxa+slippage+latencia extras.")
    print("       A estrategia tem vantagem independente de execucao perfeita.")
elif sh_worst >= 1.0:
    print("  [~~] EDGE MARGINAL")
    print("       Sharpe cai para 1-2 com custos extras.")
    print("       A estrategia funciona mas e sensivel a qualidade de execucao.")
elif sh_worst >= 0.0:
    print("  [!!] DEPENDE DE EXECUCAO PERFEITA")
    print("       Sharpe cai abaixo de 1.0 com custos extras.")
    print("       O edge esta na execucao, nao no sinal.")
else:
    print("  [XX] EDGE ILUSORIO")
    print("       Com custos extras o sistema passa a perder dinheiro.")
    print("       Os ganhos atuais nao refletem um sinal real.")

print()
print("  NOTA: notional estimado via brokerage real do historico.")
print(f"  Brokerage total: R${round(cost_brok,2)} / {N} ciclos = R${round(avg_brok,4)}/ciclo")
print(f"  Notional impl (4bps rt): R${round(notional,0)}/ciclo")
print()
