"""
SCALE READINESS ASSESSMENT — 10 Testes Antes de Escalar Capital
Avalia se o bot esta pronto para aumentar capital de forma segura.
"""
import requests, math, statistics, time, random
from datetime import datetime

API = "http://localhost:8000"
CAPITAL_ALVO = 10000  # R$ capital futuro desejado para escala

# ─────────────────────────────────────────────────────────────────────────────
# DADOS DA API
# ─────────────────────────────────────────────────────────────────────────────
print("Buscando dados locais...")
t0 = time.time()
r = requests.get(f"{API}/performance", timeout=20)
latency_ms = (time.time() - t0) * 1000
d = r.json()["data"]

N            = int(d["total_cycles"])
win_count    = int(d["win_count"])
loss_count   = int(d["loss_count"])
total_gain   = float(d["total_gain"])      # soma dos pnl positivos
total_loss   = float(d["total_loss"])      # soma dos abs(pnl negativos)
avg_pnl      = float(d["avg_pnl_per_cycle"])
sharpe_srv   = float(d["sharpe_ratio"])
capital      = float(d["current_capital"])
max_dd_pct   = abs(float(d["max_drawdown_pct"]))
costs_total  = float(d["costs_total"])
costs_brok   = float(d["costs_total_brokerage"])
costs_slip   = float(d["costs_total_slippage"])
costs_sprd   = float(d["costs_total_spread"])
recent       = d["recent_cycles"]          # last 20
wr           = win_count / N
avg_w        = total_gain / win_count  if win_count  else 0
avg_l        = total_loss / loss_count if loss_count else 0
pf_net       = total_gain / max(total_loss, 0.01)
avg_cost     = costs_total / N
notional     = (costs_brok / N) / 0.0004   # brokerage 4bps rt → notional
sigma        = (avg_pnl * math.sqrt(252)) / sharpe_srv if sharpe_srv else 1.0

recent_pnls  = [float(c["pnl"]) for c in recent]

print(f"OK — {N} ciclos | capital R${capital} | latencia API {latency_ms:.0f}ms\n")

SEP  = "=" * 66
sep  = "─" * 66
PASS = "[PASS]"
WARN = "[WARN]"
FAIL = "[FAIL]"

score_total = 0
score_max   = 0

def check(cond_pass, cond_warn, label, value="", note=""):
    global score_total, score_max
    score_max += 2
    if cond_pass:
        tag = PASS; score_total += 2
    elif cond_warn:
        tag = WARN; score_total += 1
    else:
        tag = FAIL
    v = f"  {value}" if value else ""
    n = f"\n    >> {note}" if note else ""
    print(f"  {tag}  {label}{v}{n}")
    return tag

# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  TESTE 1 — EXPECTATIVA LIQUIDA POS-CUSTOS")
print(SEP)

# Edge por trade = avg_pnl liquido / notional
edge_pct = (avg_pnl / notional) * 100 if notional > 0 else 0

# Gross return = (total_gain - total_loss) / (notional * N)
gross_return_pct = ((total_gain - total_loss) / (notional * N)) * 100

# Net return = avg_pnl / notional (ja inclui custos subtraidos)
net_return_pct = edge_pct

# Custo total em bps sobre notional
cost_bps = (avg_cost / notional) * 10000 if notional > 0 else 0

print(f"  Notional medio/ciclo   : R${notional:,.0f}")
print(f"  Custo medio/ciclo      : R${avg_cost:.4f}  ({cost_bps:.1f} bps)")
print(f"  avg_pnl liquido/ciclo  : R${avg_pnl:.4f}")
print(f"  Retorno BRUTO/ciclo    : {gross_return_pct:.4f}%")
print(f"  Retorno LIQUIDO/ciclo  : {net_return_pct:.4f}%")
print()

check(net_return_pct >= 0.15,
      net_return_pct >= 0.05,
      "Edge liquido por ciclo",
      f"{net_return_pct:.4f}% sobre notional",
      "Meta para escalar com segurança: ≥ 0.15% por ciclo")

# Simulacao: ao dobrar notional, custo de impacto cresce quadraticamente
impact_2x = avg_cost * 2 * 1.3   # 30% extra de market impact
edge_2x   = (avg_pnl - impact_2x * 1) / (notional * 2) * 100
print()
print(f"  Projecao com 2x capital: edge estimado = {edge_2x:.4f}%")
check(edge_2x >= 0.10,
      edge_2x >= 0.02,
      "Edge projetado com 2x capital",
      f"{edge_2x:.4f}%",
      "Edge deve se manter positivo ao escalar")
print()

# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  TESTE 2 — PROFIT FACTOR")
print(SEP)

pf_gross = (total_gain + costs_total * wr) / max(total_loss + costs_total * (1 - wr), 0.01)

print(f"  PF LIQUIDO  (pnl+/pnl-)   : {pf_net:.3f}  (receita={round(total_gain,2)} / perdas={round(total_loss,2)})")
print(f"  PF BRUTO    (antes custos): {pf_gross:.3f}")
print(f"  WR   : {round(wr*100,1)}%  |  avg_win: R${round(avg_w,2)}  |  avg_loss: R${round(avg_l,2)}")
print(f"  R:R  : {round(avg_w/avg_l,2) if avg_l else 0}")
print()

check(pf_net >= 2.0,
      pf_net >= 1.5,
      "Profit Factor liquido",
      f"{pf_net:.3f}",
      "PF >= 2.0 = forte | 1.5 = aceitavel | < 1.3 = fraco")

# Expectativa de Kelly
kelly_pct = (wr * avg_w - (1 - wr) * avg_l) / avg_w if avg_w > 0 else 0
kelly_pct = max(0, min(kelly_pct, 1))
safe_fraction = kelly_pct / 4  # quarter-Kelly para segurança
print()
print(f"  Kelly criterion (full)  : {kelly_pct*100:.1f}%")
print(f"  Kelly seguro (1/4)      : {safe_fraction*100:.1f}% da conta por posicao")

check(safe_fraction >= 0.05,
      safe_fraction >= 0.02,
      "Kelly seguro (1/4 Kelly)",
      f"{safe_fraction*100:.1f}% por posicao",
      "Tamanho de posicao teoricamente otimo")
print()

# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  TESTE 3 — ROBUSTEZ COM DRAWDOWN PROJETADO")
print(SEP)

print(f"  DD historico real      : {max_dd_pct:.4f}%  = R${capital * max_dd_pct/100:.2f}")
print()

# Usando distribuicao normal dos PnLs (sigma ja derivado)
# P(DD >= X%) usando estimativa de Pmax_dd via sequencias de perdas
# Abordagem: quantos ciclos consecutivos de perda para atingir X%?
# Perda media/ciclo em sequencia negativa = avg_l
for dd_target_pct, dd_label in [(1.5, "1.5%"), (3.0, "3.0%"), (5.0, "5.0%")]:
    dd_abs        = capital * dd_target_pct / 100
    # Ciclos de perda consecutiva necessarios
    cycles_needed = dd_abs / avg_l if avg_l > 0 else 999
    # Prob de sequencia dessa magnitude: P(n perdas) = (1-wr)^n
    prob_sequence = (1 - wr) ** math.ceil(cycles_needed)
    # Em 226 ciclos, esperamos ver essa sequencia quantas vezes?
    # P_at_least_once = 1 - (1 - prob_sequence)^(N - ceil(cycles_needed))
    trials = max(1, N - math.ceil(cycles_needed))
    prob_seen = 1 - (1 - prob_sequence) ** trials
    # Com N total esperado de ciclos por ano (84/dia * 252 dias)
    cycles_year = 84 * 252
    prob_year   = 1 - (1 - prob_sequence) ** max(1, cycles_year - math.ceil(cycles_needed))

    resistencia = capital - dd_abs
    print(f"  DD {dd_label:>5} = R${dd_abs:>8.2f}  |  sequencia losses: {math.ceil(cycles_needed):>2} ciclos")
    print(f"    Capital restante   : R${resistencia:,.2f}")
    print(f"    Prob. ate hoje     : {prob_seen*100:.2f}%  |  Prob. em 1 ano: {prob_year*100:.1f}%")
    print(f"    Ciclos consecutivos negativos necessarios: {math.ceil(cycles_needed)}")
    alerta = ("Aceitavel" if prob_year < 30 else "Monitorar" if prob_year < 60 else "ALTO RISCO")
    print(f"    Risco anual        : {alerta}")
    print()

# Pior sequencia observada nos ultimos 20 ciclos
max_streak_loss = 0
cur_streak = 0
for p in recent_pnls:
    if p < 0:
        cur_streak += 1
        max_streak_loss = max(max_streak_loss, cur_streak)
    else:
        cur_streak = 0

print(f"  Maior sequencia de perdas (ultimos 20 ciclos): {max_streak_loss}")
check(max_streak_loss <= 3,
      max_streak_loss <= 5,
      "Sequencia max de perdas observada",
      f"{max_streak_loss} ciclos",
      "Sequencias longas indicam risco de DD violento ao escalar")
print()

# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  TESTE 4 — STRESS DE LIQUIDEZ (Dobrar/Triplicar Lote)")
print(SEP)

print(f"  Notional atual/ciclo   : R${notional:,.0f}")
print()

# B3: spread de bid-ask cresce com tamanho. Para acoes mid/small:
# Impact = k * sqrt(Q/ADV) onde ADV ~ R$50M/dia para mid-cap
# Simplificado: para cada 2x no volume, slippage cresce ~40-70%
ADV_estimado = 50_000_000  # R$50M volume diario tipico B3 mid-cap

for mult, label in [(1, "ATUAL"), (2, "2x"), (3, "3x"), (5, "5x")]:
    vol = notional * mult
    # Participacao no ADV
    part_pct = (vol / ADV_estimado) * 100
    # Slippage estimado: base + market impact (linear para < 0.5% ADV)
    slip_extra_bps = 0 if mult == 1 else (mult - 1) * 3  # ~3bps por dobrada
    slip_total_bps = 5.0 + slip_extra_bps  # base 5bps do modelo
    edge_adj_pct   = net_return_pct - (slip_extra_bps / 100)
    print(f"  {label:<8}  notional R${vol:>10,.0f}  |  ADV% {part_pct:.3f}%  |"
          f"  slip estimado {slip_total_bps:.0f}bps  |  edge adj {edge_adj_pct:.4f}%")

print()
vol_critico = ADV_estimado * 0.005  # 0.5% do ADV = risco de impacto relevante
mult_critico = vol_critico / notional
print(f"  Limite critico (0.5% ADV): R${vol_critico:,.0f}  = {mult_critico:.1f}x o notional atual")
check(mult_critico > 5,
      mult_critico > 3,
      "Headroom de liquidez ate impacto relevante",
      f"{mult_critico:.1f}x o volume atual",
      "Abaixo de 3x: escalar vai deteriorar preco de entrada")
print()

# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  TESTE 5 — REGIME SHIFT TEST (Lateral / Alta / Queda / Noticia)")
print(SEP)

# Usar os 20 ciclos recentes para identificar segmentos de regime
# Regime detectado por retorno cumulativo da janela
def detect_regime(pnls, window=5):
    regimes = []
    for i in range(window, len(pnls)+1):
        seg = pnls[i-window:i]
        cum = sum(seg)
        wr_w = sum(1 for p in seg if p > 0) / len(seg)
        if abs(cum) < avg_l * 0.5:
            regimes.append("lateral")
        elif cum > 0:
            if wr_w > 0.7:
                regimes.append("alta_explosiva")
            else:
                regimes.append("alta_suave")
        else:
            if wr_w < 0.3:
                regimes.append("queda_abrupta")
            else:
                regimes.append("queda_suave")
    return regimes

regimes = detect_regime(recent_pnls, window=4)
regime_counts = {}
for rg in regimes:
    regime_counts[rg] = regime_counts.get(rg, 0) + 1

print(f"  Regimes detectados nos ultimos 20 ciclos (janela 4):")
for rg, cnt in sorted(regime_counts.items(), key=lambda x: -x[1]):
    pct = cnt / len(regimes) * 100
    print(f"    {rg:<20}: {cnt:>2} janelas ({pct:.0f}%)")

# Performance por segmento de regime
print()
regime_pnl = {"alta": [], "lateral": [], "queda": []}
for i, rg in enumerate(regimes):
    pnl_w = sum(recent_pnls[i:i+4]) / 4
    if "alta" in rg:
        regime_pnl["alta"].append(pnl_w)
    elif "lateral" in rg:
        regime_pnl["lateral"].append(pnl_w)
    else:
        regime_pnl["queda"].append(pnl_w)

print(f"  Performance media por regime:")
regime_pass = []
for rg_label, pnls_rg in regime_pnl.items():
    if pnls_rg:
        mu_rg = statistics.mean(pnls_rg)
        regime_pass.append(mu_rg > 0)
        sym = "+" if mu_rg > 0 else "-"
        print(f"    {rg_label:<12}: R${mu_rg:+.4f}/ciclo  {sym}")
    else:
        print(f"    {rg_label:<12}: sem dados suficientes")

regimes_positivos = sum(1 for p in regime_pass if p)
check(regimes_positivos == len(regime_pass),
      regimes_positivos >= len(regime_pass) * 0.7,
      "Positivo em multiplos regimes",
      f"{regimes_positivos}/{len(regime_pass)} regimes com mu > 0",
      "Estrategia deve gerar lucro em ao menos 2 dos 3 regimes")
print()

# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  TESTE 6 — SENSIBILIDADE DE PARAMETRO (Anti-Overfitting)")
print(SEP)

# Simular variação de SL/TP ±10% nos ciclos recentes
# Efeito: SL menor → mais trades fechados no SL (piora WR, melhora RR)
# Efeito: TP menor → mais trades fechados no TP (melhora WR, piora RR)
# Estimamos: 1% menor no TP → ~5% menos nos ganhos (trade fecha mais cedo)
# Estimamos: 1% maior no SL → ~8% mais nas perdas (trade aguenta mais antes de sair)

base_metric = avg_pnl

print(f"  Baseline avg_pnl/ciclo = R${base_metric:.4f}")
print()

sensitivities = []
for param, delta_label, effect_gain, effect_loss in [
    ("Stop  -10%",   "-10% SL",  +0.02, +0.08),  # SL menor → sai mais cedo nas perdas
    ("Stop  +10%",   "+10% SL",  -0.01, -0.06),  # SL maior → aguenta mais, perde mais
    ("Take  -10%",   "-10% TP",  -0.12, +0.03),  # TP menor → lucros menores
    ("Take  +10%",   "+10% TP",  +0.08, -0.05),  # TP maior → menos trades fecham no TP
    ("Periodo -2",   "Ind -2",   -0.07, +0.04),  # indicador mais rapido → mais ruido
    ("Periodo +2",   "Ind +2",   -0.04, +0.02),  # indicador mais lento → menos sinais
]:
    gain_adj  = total_gain  * (1 + effect_gain)
    loss_adj  = total_loss  * (1 + effect_loss)
    pnl_adj   = (gain_adj - loss_adj - costs_total) / N
    pf_adj    = gain_adj / max(loss_adj, 0.01)
    delta_pct = ((pnl_adj - base_metric) / abs(base_metric) * 100) if base_metric != 0 else 0
    sym       = "+" if pnl_adj > base_metric else ""
    flag      = "  !" if abs(delta_pct) > 50 else ""
    sensitivities.append(abs(delta_pct))
    print(f"  {param:<14}: pnl R${pnl_adj:>7.4f}  PF {pf_adj:.3f}  delta {sym}{delta_pct:.1f}%{flag}")

avg_sensitivity = statistics.mean(sensitivities)
max_sensitivity = max(sensitivities)
print()
print(f"  Sensibilidade media: {avg_sensitivity:.1f}%  |  Max: {max_sensitivity:.1f}%")

check(max_sensitivity < 30,
      max_sensitivity < 60,
      "Robustez aos parametros",
      f"max delta {max_sensitivity:.1f}%",
      "Variacao > 60%: estrategia fragil. < 30%: robusta.")
print()

# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  TESTE 7 — CORRELACAO DE PERDAS (Clustering)")
print(SEP)

# Analisar autocorrelacao de perdas nos ultimos 20 ciclos
# Se P(perda | perda anterior) >> P(perda geral) → perdas clusterizadas
losses_binary = [1 if p < 0 else 0 for p in recent_pnls]
n_recent = len(losses_binary)

# P(perda geral)
p_loss_base = 1 - wr

# P(perda | perda anterior) — lag 1
cond_loss = []
for i in range(1, n_recent):
    if losses_binary[i - 1] == 1:
        cond_loss.append(losses_binary[i])
p_loss_cond = statistics.mean(cond_loss) if cond_loss else 0

# Clustering ratio
cluster_ratio = p_loss_cond / p_loss_base if p_loss_base > 0 else 1.0

# Streaks: sequencias de perdas consecutivas
streaks = []
cur = 0
for b in losses_binary:
    if b == 1:
        cur += 1
    else:
        if cur > 0:
            streaks.append(cur)
        cur = 0
if cur > 0:
    streaks.append(cur)

max_streak  = max(streaks) if streaks else 0
avg_streak  = statistics.mean(streaks) if streaks else 0
n_streaks   = len(streaks)

# Runs test (simplificado): em distribuicao iid esperamos poucos runs longos
expected_losses = n_recent * (1 - wr)
observed_losses = sum(losses_binary)

print(f"  P(perda geral)              : {p_loss_base*100:.1f}%  (historrico 226 ciclos)")
print(f"  P(perda | perda anterior)   : {p_loss_cond*100:.1f}%  (nos ultimos 20 ciclos)")
print(f"  Clustering ratio            : {cluster_ratio:.2f}x  (>1.5 = perdas clusterizadas)")
print(f"  Maior sequencia de perdas   : {max_streak} ciclos")
print(f"  Sequencia media de perdas   : {avg_streak:.1f} ciclos")
print(f"  Numero de sequencias        : {n_streaks}")
print()

if cluster_ratio > 1.5 and max_streak >= 4:
    risco = "ALTO — perdas tendem a ocorrer em cluster"
elif cluster_ratio > 1.2:
    risco = "MODERADO — leve tendencia a clusterizacao"
else:
    risco = "BAIXO — perdas parecem independentes"
print(f"  Risco de clustering: {risco}")

check(cluster_ratio <= 1.2 and max_streak <= 3,
      cluster_ratio <= 1.5 or max_streak <= 5,
      "Independencia das perdas",
      f"cluster_ratio={cluster_ratio:.2f}, max_streak={max_streak}",
      "Clustering alto durante escala = DD violento garantido")
print()

# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  TESTE 8 — EXPOSICAO GLOBAL MAXIMA")
print(SEP)

# Baseado em Kelly e no modelo do bot
n_pos      = round(notional / (capital * 0.20)) if capital > 0 else 5  # posicoes simultaneas (20% cada)
max_simult = n_pos
exp_global = min(notional * n_pos / capital * 100, 100) if capital > 0 else 0
risco_ciclo= avg_l * n_pos  # pior caso: todos os trades passam pelo SL

print(f"  Capital atual              : R${capital:,.2f}")
print(f"  Notional estimado/ciclo    : R${notional:,.0f}  ({round(notional/capital*100,1)}% da conta)")
print(f"  Posicoes simultaneas est.  : ~{max_simult}")
print(f"  Exposicao global estimada  : {exp_global:.0f}% da conta")
print(f"  Perda maxima/ciclo (SL all): R${risco_ciclo:.2f}  ({risco_ciclo/capital*100:.2f}% da conta)")
print()

# Limites recomendados
print(f"  LIMITES RECOMENDADOS para o capital atual (R${capital:,.0f}):")
print(f"    Max por trade            : {1.0:.1f}%  = R${capital*0.01:.2f}")
print(f"    Max exposicao simultanea : {20:.0f}%  = R${capital*0.20:.2f}")
print(f"    Max perda diaria         : {3.0:.1f}%  = R${capital*0.03:.2f}")
print(f"    Max perda semanal        : {7.0:.1f}%  = R${capital*0.07:.2f}")
print()

risco_diario_obs = abs(float(d.get("today_loss", 0)))
print(f"  Perda observada hoje       : R${risco_diario_obs:.2f}")
print(f"  Limite diario sugerido     : R${capital*0.03:.2f}")

check(exp_global <= 60,
      exp_global <= 80,
      "Exposicao global vs capital",
      f"{exp_global:.0f}% (max recomendado: 60%)",
      "Exposicao > 80%: risco de margin call em volatilidade extrema")

check(risco_ciclo / capital * 100 <= 3.0,
      risco_ciclo / capital * 100 <= 5.0,
      "Pior caso por ciclo vs capital",
      f"{risco_ciclo/capital*100:.2f}% da conta",
      "Pior caso > 5% por ciclo: destruicao rapida de capital")
print()

# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  TESTE 9 — INFRAESTRUTURA (Resiliencia Operacional)")
print(SEP)

# Testar latencia real dos endpoints criticos
endpoints = [
    ("/health",          "Health check"),
    ("/performance",     "Performance"),
    ("/scheduler/status","Scheduler"),
    ("/trade/status",    "Trade state"),
]

infra_results = []
for path, label in endpoints:
    try:
        t_start = time.time()
        resp    = requests.get(f"{API}{path}", timeout=10)
        ms      = (time.time() - t_start) * 1000
        ok      = resp.status_code == 200
        infra_results.append((label, ms, ok))
        status  = "OK" if ok else f"HTTP {resp.status_code}"
        flag    = "" if ms < 500 else "  [LENTO]" if ms < 2000 else "  [CRITICO]"
        print(f"  {label:<20}: {ms:>6.0f}ms  {status}{flag}")
    except Exception as ex:
        infra_results.append((label, 9999, False))
        print(f"  {label:<20}:  TIMEOUT / ERRO  {str(ex)[:40]}")

# Verificar scheduler
try:
    rs = requests.get(f"{API}/scheduler/status", timeout=10).json()
    running = rs.get("running", False)
    cycle_ms = rs.get("cycle_interval_sec", 0) * 1000
    print(f"\n  Scheduler rodando         : {'SIM' if running else 'NAO'}")
    print(f"  Intervalo de ciclo        : {rs.get('cycle_interval_sec', '?')}s")
except:
    running = False
    print(f"\n  Scheduler: nao foi possivel verificar")

max_latency = max(ms for _, ms, _ in infra_results)
all_ok      = all(ok for _, _, ok in infra_results)
lat_ok      = max_latency < 500

print()
print(f"  Max latencia API           : {max_latency:.0f}ms  ({'aceitavel' if max_latency < 500 else 'LENTA'})")
print(f"  Latencia da medicao inicial: {latency_ms:.0f}ms")

# Verificar comportamento com dado antigo (safety check)
try:
    rt = requests.get(f"{API}/trade/status", timeout=10).json()
    last_cycle_raw = rt.get("data", {}).get("last_update", "") or ""
    if last_cycle_raw:
        # Calcular stale em segundos
        try:
            from datetime import timezone, timedelta
            brt = timezone(timedelta(hours=-3))
            last_dt = datetime.fromisoformat(last_cycle_raw.replace("Z", "+00:00"))
            now_brt = datetime.now(brt)
            stale_s = (now_brt - last_dt.astimezone(brt)).total_seconds()
            print(f"  Ultimo update do estado    : {round(stale_s)}s atras")
            stale_ok = stale_s < 300
            print(f"  Estado fresco (<5min)      : {'SIM' if stale_ok else 'NAO — dado ANTIGO!'}")
        except:
            stale_ok = True
    else:
        stale_ok = True
except:
    stale_ok = True

check(all_ok and lat_ok and stale_ok,
      all_ok and max_latency < 2000,
      "Infraestrutura e latencia",
      f"max {max_latency:.0f}ms, todos OK: {all_ok}",
      "Bot com dado antigo ou API lenta = ordens erradas ao escalar")
print()

# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  TESTE 10 — PLANO DE CAPITAL PROGRESSIVO")
print(SEP)

# Calcular onde estamos na trajetoria
meta_ciclos   = 800
meta_sharpe   = 2.0
meta_pf       = 1.7
meta_dd       = 5.0  # maximo dd tolerado em escala

pct_ciclos = min(N / meta_ciclos * 100, 100)
pct_sharpe = min(sharpe_srv / meta_sharpe * 100, 100)
pct_pf     = min(pf_net    / meta_pf     * 100, 100)

pct_overall = (pct_ciclos + pct_sharpe + pct_pf) / 3

# Etapas do escalonamento
stages = [
    (0.10,  30,  "Etapa 1"),
    (0.25,  60,  "Etapa 2"),
    (0.50,  120, "Etapa 3"),
    (1.00,  999, "Escala total"),
]

print(f"  Capital atual: R${capital:,.2f}  |  Capital alvo: R${CAPITAL_ALVO:,.2f}")
print()
print(f"  PROGRESSO PARA META MINIMA DE ESCALA:")
print(f"    Ciclos     : {N:>4} / {meta_ciclos}    {pct_ciclos:>5.1f}%  {'[OK]' if N >= meta_ciclos else '[--]'}")
print(f"    Sharpe     : {sharpe_srv:>6.2f} / {meta_sharpe:.1f}   {pct_sharpe:>5.1f}%  {'[OK]' if sharpe_srv >= meta_sharpe else '[--]'}")
print(f"    Prof Factor: {pf_net:>6.3f} / {meta_pf:.1f}   {pct_pf:>5.1f}%  {'[OK]' if pf_net >= meta_pf else '[--]'}")
print(f"    Progresso geral: {pct_overall:.1f}%")
print()

print(f"  PLANO PROGRESSIVO (baseado no capital alvo R${CAPITAL_ALVO:,.0f}):")
for frac, dias, label in stages:
    cap_stage = CAPITAL_ALVO * frac
    duration  = f"{dias} dias" if dias < 999 else "definitivo"
    criterio  = "Ciclos OK + DD < 2%"
    print(f"    {label}: {frac*100:>4.0f}% = R${cap_stage:>8,.0f}  por {duration}")

print()
print(f"  DECISAO ATUAL:")
if N < 300:
    stage_now = "Etapa 1 (10%)"
    capital_recomendado = CAPITAL_ALVO * 0.10
elif N < 500 and pf_net < 1.5:
    stage_now = "Etapa 1→2 (entre 10% e 25%)"
    capital_recomendado = CAPITAL_ALVO * 0.15
elif N < 800 and pf_net >= 1.5:
    stage_now = "Etapa 2 (25%)"
    capital_recomendado = CAPITAL_ALVO * 0.25
else:
    stage_now = "Etapa 3 (50%)"
    capital_recomendado = CAPITAL_ALVO * 0.50

print(f"    Com {N} ciclos, PF {pf_net:.2f}, voce esta em: {stage_now}")
print(f"    Capital recomendado agora : R${capital_recomendado:,.0f}")
print(f"    Capital atual             : R${capital:,.0f}")
diff = capital_recomendado - capital
print(f"    Diferenca                 : R${diff:+,.0f}")

check(N >= meta_ciclos and pf_net >= meta_pf and sharpe_srv >= meta_sharpe,
      N >= 300 and pf_net >= 1.3,
      "Maturidade para escala",
      f"{N} ciclos / PF {pf_net:.2f} / Sharpe {sharpe_srv:.2f}",
      f"Meta minima: {meta_ciclos} ciclos + PF>={meta_pf} + Sharpe>={meta_sharpe}")
print()

# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print(SEP)
print("  RESULTADO FINAL — SCALE READINESS SCORE")
print(SEP)
print(SEP)

score_pct = score_total / score_max * 100 if score_max > 0 else 0

print(f"\n  Score: {score_total}/{score_max}  =  {score_pct:.0f}%")
print()

if score_pct >= 80:
    nivel = "PRONTO PARA ESCALAR"
    cor   = "Todos os criterios principais aprovados."
elif score_pct >= 60:
    nivel = "QUASE PRONTO — corrija os pontos [WARN] e [FAIL]"
    cor   = "Progresso solido. Alguns criterios precisam melhorar antes."
elif score_pct >= 40:
    nivel = "EM DESENVOLVIMENTO — nao escale ainda"
    cor   = "Varios criterios criticos ainda nao atingidos."
else:
    nivel = "NAO ESCALE — risco alto de destruicao de capital"
    cor   = "Estrategia nao validada para capital real significativo."

print(f"  STATUS: {nivel}")
print(f"  {cor}")
print()
print(f"  RESUMO DOS TESTES:")
print(f"    [PASS] = 2 pts  [WARN] = 1 pt  [FAIL] = 0 pts")
print()

print("  PROXIMOS PASSOS PRIORITARIOS:")
if N < 800:
    print(f"  1. Continue coletando ciclos: {N}/800  (faltam {800-N})")
if sharpe_srv < 2.0 or score_pct < 60:
    print(f"  2. Ajuste o custo de slippage no _TRADING_COST_MODEL")
    print(f"     (slippage atual 2bps e irreal para B3 mid-cap; use 8-12bps)")
if pf_net < 1.7:
    print(f"  3. Melhore o R:R ratio aumentando TP ou melhorando filtros de entrada")
    print(f"     R:R atual: {round(avg_w/avg_l,2)} | Meta: 2.0+")
if cluster_ratio > 1.3:
    print(f"  4. Implemente circuit-breaker: pause apos {max_streak} losses consecutivas")
print(f"  5. Rode o plano progressivo: comece com R${capital_recomendado:,.0f}")
print()
