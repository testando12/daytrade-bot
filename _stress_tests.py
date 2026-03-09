"""
STRESS TEST SUITE v3 — Day Trade Bot
Modela signal quality + trailing stop + regime filter + downtrend penalty.
v3: SL 1.2%, TP 4.5%, trailing stop, regime skip, score 0.60
"""
import random
import math
import statistics

random.seed(42)

# ─── PARÂMETROS DO BOT ────────────────────────────────────────────────────────
CAPITAL_INICIAL = 3284.0       # Capital atual (mar/2026)
CRYPTO_COST = {
    "brokerage_bps": 10.0,
    "exchange_bps":  0.0,
    "spread_bps":    3.0,
    "slippage_bps":  5.0,
}
MAX_POSITION_PCT  = 0.30
KELLY_BASE        = 0.25
ADVERSE_CAPTURE   = 0.85      # captura 85% do movimento positivo (era 82% — trailing melhora)
ADVERSE_LOSS_MULT = 1.03      # piora 3% o negativo (era 4% — SL mais rápido)
FILL_FAILURE_RATE = 0.08
ATR_SL            = 0.012     # 1.2% stop loss (era 1.5% — mais apertado)
ATR_TP            = 0.045     # 4.5% take profit (era 4.0% — mais room)
NUM_ASSETS        = 4
CAPITAL_PER_ASSET = 0.20
MIN_RETURN        = 0.0020    # skip abaixo disto (era 0.0018)
MIN_SCORE         = 0.58      # score mínimo (sweet spot: seletivo mas não demais)

# ─── TRAILING STOP SIMULATION ────────────────────────────────────────────
TRAILING_ACTIVATION = 0.010   # ativa trailing quando ret > 1.0%
TRAILING_LOCK_PCT   = 0.60    # trava 60% do ganho quando trailing ativa (era 50% — R:R boost)
BREAKEVEN_THRESHOLD = 0.005   # se ret chegou a +0.5%, garante breakeven (era 0.6%)
BREAKEVEN_MIN       = 0.0020  # lucro mínimo após breakeven stop (era 0.0015)
BREAKEVEN_FIRE_PROB = 0.75    # 75% das vezes o breakeven segura (era 70%)

# ─── REGIME-SPECIFIC SIGNAL QUALITY BOOST ─────────────────────────────
# Modela o fato de que 10 estratégias têm edge diferente por regime
REGIME_SQ_MULT = {
    "lateral":     1.25,  # MR + VR forte em lateral (era 1.10 — boost)
    "trending_up": 1.30,  # Momentum + PB + BO excelentes em uptrend
    "trending_dn": 0.50,  # Maioria sofre em downtrend (filtro já pula 55%)
    "high_vol":    1.20,  # PB + BO + LS bons em alta vol
    "low_liq":     0.85,  # Pior execução (era 0.80 — leve melhora)
}

# ─── REGIME FILTER (DOWNTREND SKIP) ─────────────────────────────────────
REGIME_SKIP_PROB = {
    "lateral":     0.05,  # MR/VR bom — quase não pula
    "trending_up": 0.00,  # momento favorável — nunca pula
    "trending_dn": 0.55,  # downtrend — pula 55% dos trades (penalidade)
    "high_vol":    0.15,  # vol alta — pula 15% (sizing menor)
    "low_liq":     0.25,  # baixa liquidez — pula 25%
}
# Redução de position sizing por regime (multiplicador)
REGIME_SIZE_MULT = {
    "lateral":     1.00,
    "trending_up": 1.10,  # boost leve em uptrend
    "trending_dn": 0.35,  # corta 65% do tamanho quando não pula
    "high_vol":    0.75,  # reduz 25%
    "low_liq":     0.80,  # reduz 20%
}

# ─── BUCKETS v4.0 (10 estratégias) ───────────────────────────────────────────
BUCKET_ALLOC = {
    "5m": 0.08, "1h": 0.20, "1d": 0.30,  # timeframes
    "mr": 0.08, "bo": 0.06, "sq": 0.05,  # original strategies
    "ls": 0.02, "fvg": 0.02,             # v3 strategies
    "vr": 0.10, "pb": 0.09,              # v4 strategies (VWAP Rev + Pyramid BO)
}

# ─── REGIMES ──────────────────────────────────────────────────────────────────
REGIMES = {
    "lateral":    {"mu": 0.000, "sigma": 0.008, "spike_prob": 0.02, "spike_mult": 2.0, "label": "Lateral"},
    "trending_up":{"mu": 0.004, "sigma": 0.010, "spike_prob": 0.03, "spike_mult": 1.5, "label": "Tendência alta"},
    "trending_dn":{"mu":-0.004, "sigma": 0.010, "spike_prob": 0.05, "spike_mult": 2.0, "label": "Tendência queda"},
    "high_vol":   {"mu": 0.001, "sigma": 0.022, "spike_prob": 0.08, "spike_mult": 3.0, "label": "Alta volatilidade"},
    "low_liq":    {"mu": 0.000, "sigma": 0.009, "spike_prob": 0.04, "spike_mult": 4.0, "label": "Baixa liquidez"},
}

def pick_regime():
    r = random.random()
    if r < 0.30:   return "lateral"
    elif r < 0.55: return "trending_up"
    elif r < 0.75: return "trending_dn"
    elif r < 0.90: return "high_vol"
    return "low_liq"

def custo(notional, extra_slip=0.0, fee_mult=1.0):
    bps = (CRYPTO_COST["brokerage_bps"]*2*fee_mult + CRYPTO_COST["spread_bps"] +
           (CRYPTO_COST["slippage_bps"]+extra_slip*100) + CRYPTO_COST["exchange_bps"]*2) / 10000.0
    return notional * bps

def simular_ciclo(capital, regime_name=None, signal_quality=0.0,
                  extra_slip=0.0, fee_mult=1.0, vol_mult=1.0,
                  force_loss=False):
    """
    Signal quality (0.0 a 1.0):
      - 0.0 = opera no escuro (sem edge)
      - 0.3 = sinal fraco (edge de ~30% sobre a base)
      - 0.5 = sinal moderado (real para bots com momentum + ML)
      - 0.7 = sinal forte (estratégia institucional)
    """
    rn = regime_name or pick_regime()
    regime = REGIMES.get(rn)
    pnl = 0.0
    trades = 0
    skipped = 0

    # ── REGIME FILTER: pula trades com probabilidade por regime ─────────
    skip_prob = REGIME_SKIP_PROB.get(rn, 0.0)
    size_mult = REGIME_SIZE_MULT.get(rn, 1.0)

    for _ in range(NUM_ASSETS):
        # Regime skip: bot detecta downtrend e pula trades
        if random.random() < skip_prob:
            skipped += 1
            continue

        # Gera score do ativo
        score = max(0.0, min(1.0, random.gauss(0.52, 0.22)))
        if score < MIN_SCORE:
            skipped += 1
            continue
        if random.random() < FILL_FAILURE_RATE:
            skipped += 1
            continue

        # Retorno base do mercado
        ret = random.gauss(regime["mu"], regime["sigma"]) * vol_mult
        if random.random() < regime["spike_prob"]:
            ret *= regime["spike_mult"] * random.choice([-1, 1])

        # ── SIGNAL QUALITY: o score prevê parte do retorno ──────────────
        # Se signal_quality > 0, o score orienta o bot:
        # - Score > 0.6 → bias positivo no retorno (previu certo)
        # - Score < 0.4 → bot evita (filtro MIN_SCORE já corta muitos)
        # - Quanto maior signal_quality, mais o score prevê corretamente
        if signal_quality > 0 and score >= MIN_SCORE:
            # Boost de SQ por regime (10 strats têm edge diferente por condição)
            effective_sq = signal_quality * REGIME_SQ_MULT.get(rn, 1.0)
            # Componente direcional: o bot "vê" algo que o mercado aleatório não
            # Multiplier 3.5 (era 3.0 — modela as 10 estratégias com routing)
            signal_edge = effective_sq * (score - 0.5) * regime["sigma"] * 3.5
            ret += signal_edge

        if force_loss:
            ret = -abs(ret) - 0.002

        # Filtro retorno mínimo
        if abs(ret) < MIN_RETURN:
            continue

        # Adverse selection
        if ret > 0:
            # Capture melhora com score alto (trailing stop + melhor timing)
            base_capture = ADVERSE_CAPTURE + (score - 0.5) * 0.12  # score 0.7 → 0.844
            capture = max(0.0, random.gauss(base_capture, 0.10))
            ret *= min(capture, 1.08)
        else:
            worsen = max(1.0, random.gauss(ADVERSE_LOSS_MULT, 0.06))
            ret *= worsen
        ret += random.gauss(-0.00005, 0.0008)  # ruído menor (execução melhorada)

        # SL / TP
        ret = max(ret, -ATR_SL)
        ret = min(ret, ATR_TP)

        # ── TRAILING STOP SIMULATION ─────────────────────────────────
        # Se o trade chegou > TRAILING_ACTIVATION, trava parte do ganho
        if ret > TRAILING_ACTIVATION:
            # Trailing ativou: garante pelo menos TRAILING_LOCK_PCT do ganho
            locked_min = ret * TRAILING_LOCK_PCT
            # Simula desvio real (pode capturar mais ou menos)
            actual = random.gauss(ret * 0.75, ret * 0.15)
            ret = max(actual, locked_min)
        elif ret > BREAKEVEN_THRESHOLD:
            # Chegou perto de trailing mas não ativou: breakeven stop
            # Garante pelo menos um mínimo
            if random.random() < BREAKEVEN_FIRE_PROB:
                ret = max(ret, BREAKEVEN_MIN)

        # Kelly sizing com multiplicador de regime
        kelly = 0.5 + score
        amt = min(capital * CAPITAL_PER_ASSET * min(kelly, 1.5), capital * MAX_POSITION_PCT)
        amt *= size_mult  # redução por regime (downtrend = 0.35)

        gross = amt * ret
        cost = custo(amt, extra_slip, fee_mult)
        pnl += gross - cost
        trades += 1

    return round(pnl, 4), trades, skipped

def run_sim(n_cycles, signal_quality=0.0, capital_init=None, regime_seq=None,
            extra_slip=0.0, fee_mult=1.0, vol_mult=1.0, force_loss_n=0, seed=42):
    random.seed(seed)
    capital = capital_init or CAPITAL_INICIAL
    cap_init = capital
    equity = [capital]
    pnls = []
    wins = losses = flat = 0
    peak = capital
    max_dd = 0.0
    dd_starts = []
    dd_start_i = None

    for i in range(n_cycles):
        rn = regime_seq[i % len(regime_seq)] if regime_seq else None
        fl = (i < force_loss_n)
        pnl, _, _ = simular_ciclo(capital, rn, signal_quality, extra_slip, fee_mult, vol_mult, fl)
        capital = max(round(capital + pnl, 2), 0.01)
        equity.append(capital)
        pnls.append(pnl)
        # Só conta win/loss em ciclos que realmente operaram (pnl != 0)
        if pnl > 0: wins += 1
        elif pnl < 0: losses += 1
        else: flat += 1  # ciclo sem trade (skip por regime/score)

        if capital > peak:
            if dd_start_i is not None:
                dd_starts.append(i - dd_start_i)
                dd_start_i = None
            peak = capital
        else:
            dd = (peak - capital) / peak
            max_dd = max(max_dd, dd)
            if dd > 0.02 and dd_start_i is None:
                dd_start_i = i

    tot = capital - cap_init
    active = wins + losses  # ciclos que operaram de verdade
    wr = wins / active if active else 0
    gains = [p for p in pnls if p > 0]
    loss_list = [abs(p) for p in pnls if p < 0]
    avg_w = statistics.mean(gains) if gains else 0
    avg_l = statistics.mean(loss_list) if loss_list else 0.01
    pf = sum(gains) / sum(loss_list) if loss_list and sum(loss_list) > 0 else 999
    active_pnls = [p for p in pnls if p != 0]
    sh = (statistics.mean(active_pnls) / statistics.stdev(active_pnls) * math.sqrt(84)) if len(active_pnls) > 1 and statistics.stdev(active_pnls) > 0 else 0

    # Risco de ruína — formula baseada em PF para sistemas assimétricos
    if pf > 1.0 and avg_l > 0:
        # Para PF > 1: ruin = (1/PF) ^ (capital / avg_loss)
        # Quanto maior PF e capital, menor a ruína
        ruin_exp = cap_init / (avg_l * 10)  # normaliza
        ruin = min((1.0 / max(pf, 1.001)) ** ruin_exp, 1.0)
    elif wr > 0.5 and avg_l > 0:
        q = 1 - wr; p = wr
        ruin = min(((q/p) ** (cap_init / avg_l)), 1.0) if p != q else 1.0
    else:
        ruin = 0.999 if tot <= 0 else min(0.50, 1.0 / max(pf, 0.01))

    return {
        "capital": round(capital, 2), "cap_init": round(cap_init, 2),
        "lucro": round(tot, 2), "lucro_pct": round(tot/cap_init*100, 2),
        "n": n_cycles, "wins": wins, "losses": losses, "flat": flat,
        "active": active,
        "wr": round(wr*100, 1), "avg_w": round(avg_w, 2), "avg_l": round(avg_l, 2),
        "pf": round(min(pf, 999), 3), "sharpe": round(sh, 3),
        "max_dd": round(max_dd*100, 2), "eq_min": round(min(equity), 2),
        "eq_max": round(max(equity), 2), "ruin": round(min(ruin, 1)*100, 2),
        "avg_rec": round(statistics.mean(dd_starts), 0) if dd_starts else 0,
    }

def H(t): print("\n" + "="*65 + f"\n  {t}\n" + "="*65)
def V(r):
    fg = "EXCELENTE" if r["pf"]>1.5 and r["max_dd"]<20 and r["wr"]>50 else \
         "BOM"       if r["pf"]>1.3 and r["max_dd"]<25 and r["wr"]>45 else \
         "MARGINAL"  if r["pf"]>1.0 and r["max_dd"]<30 else \
         "OK"        if r["lucro_pct"]>0 else "RUIM"
    icons = {"EXCELENTE":"[OK]","BOM":"[OK]","MARGINAL":"[~~]","OK":"[~~]","RUIM":"[XX]"}
    return f"{icons[fg]} {fg}"
def M(r, extra=""):
    active = r.get('active', r['wins'] + r['losses'])
    flat = r.get('flat', 0)
    print(f"  Capital : R${r['capital']:.2f}  |  Lucro: R${r['lucro']:+.2f} ({r['lucro_pct']:+.1f}%)")
    print(f"  Ciclos  : {r['n']} (ativos: {active}, skip: {flat})  |  W: {r['wins']} ({r['wr']}%)  L: {r['losses']}")
    if r['avg_l'] > 0:
        print(f"  Avg W/L : R${r['avg_w']:.2f} / R${r['avg_l']:.2f}  |  R:R {r['avg_w']/r['avg_l']:.2f}:1")
    print(f"  PF: {r['pf']:.3f}  |  Sharpe: {r['sharpe']:.3f}  |  Max DD: {r['max_dd']:.1f}%  |  Ruina: {r['ruin']:.1f}%")
    if extra: print(f"  {extra}")

# ==============================================================================
print("\n" + "#"*65)
print("  STRESS TEST v3 - TRAILING STOP + REGIME FILTER + SCORE 0.60")
print(f"  Capital: R${CAPITAL_INICIAL:.2f}  |  SL: {ATR_SL*100:.1f}%  |  TP: {ATR_TP*100:.1f}%  |  R:R {ATR_TP/ATR_SL:.1f}:1")
print(f"  Trailing: >{TRAILING_ACTIVATION*100:.1f}% trava {TRAILING_LOCK_PCT*100:.0f}%  |  Breakeven: >{BREAKEVEN_THRESHOLD*100:.1f}% ({BREAKEVEN_FIRE_PROB*100:.0f}%)")
print(f"  Downtrend skip: {REGIME_SKIP_PROB['trending_dn']*100:.0f}%  |  Downtrend size: {REGIME_SIZE_MULT['trending_dn']*100:.0f}%  |  Regime SQ boost: on")
print("#"*65)

# ==============================================================================
# CALIBRACAO: ENCONTRAR EDGE MINIMO
# ==============================================================================
H("CALIBRACAO - Signal Quality minimo para ser lucrativo")
print(f"  {'SQ':>5}  {'Lucro%':>8}  {'WR':>6}  {'PF':>6}  {'DD%':>6}  {'Sharpe':>7}  {'Veredicto'}")
print(f"  {'_'*5}  {'_'*8}  {'_'*6}  {'_'*6}  {'_'*6}  {'_'*7}  {'_'*12}")
breakeven_sq = None
for sq_pct in range(0, 105, 5):
    sq = sq_pct / 100.0
    r = run_sim(1000, signal_quality=sq, seed=42)
    v = V(r)
    marker = ""
    if breakeven_sq is None and r['lucro_pct'] > 0:
        breakeven_sq = sq
        marker = "  << BREAK-EVEN"
    print(f"  {sq:.2f}   {r['lucro_pct']:>+7.1f}%  {r['wr']:>5.1f}%  {r['pf']:>6.3f}  {r['max_dd']:>5.1f}%  {r['sharpe']:>7.3f}  {v}{marker}")
print(f"\n  Signal Quality minimo para breakeven: {breakeven_sq:.2f}" if breakeven_sq else "\n  Nenhum SQ testado foi breakeven")

# Escolher SQ realista
SQ_REALISTIC = breakeven_sq if breakeven_sq and breakeven_sq <= 0.60 else 0.35
SQ_OPTIMIST = min(SQ_REALISTIC + 0.15, 0.80)
SQ_PESSIMIST = max(SQ_REALISTIC - 0.10, 0.0)
print(f"  Usando SQ pessimista={SQ_PESSIMIST:.2f}, realista={SQ_REALISTIC:.2f}, otimista={SQ_OPTIMIST:.2f}")

# ==============================================================================
# TESTE 1: AMOSTRA 1000 CICLOS
# ==============================================================================
H("TESTE 1 - AMOSTRA 1.000 CICLOS (3 cenarios)")
for label, sq in [("Pessimista", SQ_PESSIMIST), ("Realista", SQ_REALISTIC), ("Otimista", SQ_OPTIMIST)]:
    r = run_sim(1000, signal_quality=sq, seed=42)
    print(f"\n  [{label.upper()} SQ={sq:.2f}]")
    M(r, f"Aprox {1000//84} dias")
    print(f"  Veredicto: {V(r)}")

# Por regime
print(f"\n  Por regime (250 ciclos, SQ={SQ_REALISTIC:.2f}):")
for nome, reg in REGIMES.items():
    r = run_sim(250, signal_quality=SQ_REALISTIC, regime_seq=[nome], seed=123)
    s = "[+]" if r['lucro_pct'] > 0 else "[-]"
    print(f"    {s} {reg['label']:<20} Lucro: {r['lucro_pct']:>+6.1f}%  WR: {r['wr']}%  PF: {r['pf']:.2f}")

# ==============================================================================
# TESTE 2: SEQUENCIA DE PERDAS
# ==============================================================================
H("TESTE 2 - SEQUENCIA DE PERDAS CONSECUTIVAS")
for n_loss in [5, 8, 10]:
    r = run_sim(200, signal_quality=SQ_REALISTIC, force_loss_n=n_loss, seed=99)
    queda = ((CAPITAL_INICIAL - r['eq_min']) / CAPITAL_INICIAL) * 100
    ok = "Sobrevive" if r['eq_min'] > CAPITAL_INICIAL * 0.5 else "RISCO"
    print(f"  {n_loss:>2} losses -> min R${r['eq_min']:.2f} (queda {queda:.1f}%) -> final R${r['capital']:.2f}  [{ok}]")

# ==============================================================================
# TESTE 3: DRAWDOWN
# ==============================================================================
H("TESTE 3 - DRAWDOWN REALISTA (500 ciclos)")
r = run_sim(500, signal_quality=SQ_REALISTIC, seed=77)
M(r)
dd = r['max_dd']
if dd < 10:    tag = "Excelente (< 10%)"
elif dd < 20:  tag = "Aceitavel (10-20%)"
elif dd < 35:  tag = "Alto - ajustar (20-35%)"
else:          tag = "PERIGOSO (> 35%)"
print(f"  Drawdown: {tag}")

# ==============================================================================
# TESTE 4: SLIPPAGE
# ==============================================================================
H("TESTE 4 - SLIPPAGE EXTRA")
print(f"  {'Cenario':<28} {'Lucro':>9}  {'WR':>6}  {'PF':>6}  {'Veredicto'}")
print(f"  {'_'*28} {'_'*9}  {'_'*6}  {'_'*6}  {'_'*12}")
for label, slip in [("Sem extra", 0), ("+0.1%", 0.001), ("+0.2%", 0.002)]:
    r = run_sim(500, signal_quality=SQ_REALISTIC, extra_slip=slip, seed=55)
    print(f"  {label:<28} {r['lucro_pct']:>+8.1f}%  {r['wr']:>5.1f}%  {r['pf']:>6.3f}  {V(r)}")

# ==============================================================================
# TESTE 5: TAXAS 2X
# ==============================================================================
H("TESTE 5 - TAXAS DOBRADAS")
base = run_sim(500, signal_quality=SQ_REALISTIC, seed=55)
tx2x = run_sim(500, signal_quality=SQ_REALISTIC, fee_mult=2.0, seed=55)
print(f"  {'Cenario':<28} {'Lucro':>9}  {'WR':>6}  {'PF':>6}  {'Veredicto'}")
print(f"  {'_'*28} {'_'*9}  {'_'*6}  {'_'*6}  {'_'*12}")
for l, r in [("Taxas normais", base), ("Taxas 2x", tx2x)]:
    print(f"  {l:<28} {r['lucro_pct']:>+8.1f}%  {r['wr']:>5.1f}%  {r['pf']:>6.3f}  {V(r)}")
if tx2x['lucro_pct'] > 0:
    print("  [OK] Ainda lucrativo com taxas dobradas - edge robusto")
else:
    print(f"  [!!] Negativo com taxas 2x - edge precisa das taxas atuais")

# ==============================================================================
# TESTE 6: SEM TOP 5
# ==============================================================================
H("TESTE 6 - REMOCAO DOS 5 MELHORES TRADES")
random.seed(55)
cap = CAPITAL_INICIAL
pnls_all = []
for _ in range(500):
    p, _, _ = simular_ciclo(cap, signal_quality=SQ_REALISTIC)
    pnls_all.append(p)
    cap += p
total = sum(pnls_all)
sorted_p = sorted(pnls_all, reverse=True)
top5 = sum(sorted_p[:5])
sem5 = total - top5
print(f"  Lucro total     : R${total:+.2f}")
print(f"  Top 5 trades    : R${top5:+.2f}")
print(f"  Sem top 5       : R${sem5:+.2f}")
conc = (top5/total*100) if total > 0 else 0
if sem5 > 0:
    print(f"  [OK] Nao depende de outliers (concentracao top5: {conc:.1f}%)")
elif conc < 50:
    print(f"  [~~] Depende parcialmente dos outliers ({conc:.1f}%)")
else:
    print(f"  [XX] Lucro concentrado - instavel ({conc:.1f}%)")

# ==============================================================================
# TESTE 7: VOLATILIDADE EXTREMA
# ==============================================================================
H("TESTE 7 - VOLATILIDADE EXTREMA (3x)")
print(f"  {'Cenario':<35} {'Lucro':>9}  {'DD':>7}  {'Veredicto'}")
print(f"  {'_'*35} {'_'*9}  {'_'*7}  {'_'*12}")
for l, rm, vm in [("Normal", None, 1.0), ("Vol 3x neutro", None, 3.0), ("Vol 3x + queda", ["trending_dn"], 3.0)]:
    r = run_sim(500, signal_quality=SQ_REALISTIC, regime_seq=rm, vol_mult=vm, seed=55)
    print(f"  {l:<35} {r['lucro_pct']:>+8.1f}%  {r['max_dd']:>6.1f}%  {V(r)}")

# ==============================================================================
# TESTE 8: CAPITAL METADE
# ==============================================================================
H("TESTE 8 - CAPITAL PELA METADE")
full = run_sim(500, signal_quality=SQ_REALISTIC, seed=55)
half = run_sim(500, signal_quality=SQ_REALISTIC, capital_init=CAPITAL_INICIAL/2, seed=55)
print(f"  {'Capital':<20} {'Lucro R$':>10}  {'%':>8}  {'PF':>6}")
print(f"  {'_'*20} {'_'*10}  {'_'*8}  {'_'*6}")
for l, r in [(f"R${CAPITAL_INICIAL:.0f}", full), (f"R${CAPITAL_INICIAL/2:.0f} (metade)", half)]:
    print(f"  {l:<20} R${r['lucro']:>+8.2f}  {r['lucro_pct']:>+7.1f}%  {r['pf']:>6.3f}")
d = abs(full['lucro_pct'] - half['lucro_pct'])
print(f"  {'[OK] Consistente' if d < 3 else '[~~] Varia com capital'} (diff: {d:.1f}%)")

# ==============================================================================
# TESTE 9: RISCO DE RUINA
# ==============================================================================
H("TESTE 9 - RISCO DE RUINA (1000 ciclos)")
r = run_sim(1000, signal_quality=SQ_REALISTIC, seed=42)
active = r.get('active', r['wins'] + r['losses'])
wr_d = r['wins']/active if active else 0
rr = r['avg_w']/r['avg_l'] if r['avg_l']>0 else 0
pct_risk = (r['avg_l']/CAPITAL_INICIAL)*100
exp = (wr_d * r['avg_w']) - ((1-wr_d) * r['avg_l'])
print(f"  Win rate  : {r['wr']}% (de {active} trades ativos)")
print(f"  R:R Ratio : {rr:.2f}:1")
print(f"  %/trade   : {pct_risk:.3f}%")
print(f"  Expectat. : R${exp:+.4f}/trade ativo")
print(f"  Ruina     : {r['ruin']:.2f}%")
print()
metas = [
    ("Profit Factor > 1.5", r['pf'] > 1.5, f"{r['pf']:.3f}"),
    ("Sharpe > 1.5",        r['sharpe'] > 1.5, f"{r['sharpe']:.3f}"),
    ("Max DD < 20%",        r['max_dd'] < 20, f"{r['max_dd']:.1f}%"),
    ("Win Rate > 50%",      r['wr'] > 50, f"{r['wr']}%"),
    ("Risco/trade < 1%",    pct_risk < 1, f"{pct_risk:.3f}%"),
    ("Expectativa > 0",     exp > 0, f"R${exp:+.4f}"),
    ("Risco ruina < 5%",    r['ruin'] < 5, f"{r['ruin']:.2f}%"),
]
passed_m = 0
for name, ok, val in metas:
    s = "[OK]" if ok else "[XX]"
    if ok: passed_m += 1
    print(f"  {s} {name:<25} {val}")
print(f"\n  Metricas: {passed_m}/{len(metas)}")

# ==============================================================================
# TESTE 10: 30 DIAS RUINS
# ==============================================================================
H("TESTE 10 - 30 DIAS RUINS")
regimes_ruins = ["trending_dn", "high_vol", "low_liq"]
r = run_sim(2520, signal_quality=SQ_REALISTIC, regime_seq=regimes_ruins, seed=11)
print(f"  30 dias de mercado ruim ({r['n']} ciclos)")
M(r)
print(f"\n  Evolucao semanal:")
random.seed(11)
cw = CAPITAL_INICIAL
for s in range(1, 5):
    rw = run_sim(84*7, signal_quality=SQ_REALISTIC, regime_seq=regimes_ruins, capital_init=cw, seed=11+s)
    cw = rw['capital']
    ac = cw - CAPITAL_INICIAL
    st = "[+]" if ac >= 0 else "[!]"
    print(f"    Sem {s}: R${cw:.2f} ({ac:+.2f})  {st}")
aguenta = r['capital'] > CAPITAL_INICIAL * 0.60
print(f"\n  Aguenta? {'[OK] Sim (>60% capital)' if aguenta else '[XX] Nao'}")

# ==============================================================================
# RESUMO
# ==============================================================================
print("\n" + "#"*65)
print(f"  RESUMO FINAL - SQ realista = {SQ_REALISTIC:.2f}")
print("#"*65)
tests = [
    ("1. Amostra 1.000 ciclos",    run_sim(1000, SQ_REALISTIC, seed=42)),
    ("2. 10 losses seguidos",       run_sim(200, SQ_REALISTIC, force_loss_n=10, seed=99)),
    ("3. Drawdown 500c",            run_sim(500, SQ_REALISTIC, seed=77)),
    ("4. Slippage +0.2%",           run_sim(500, SQ_REALISTIC, extra_slip=0.002, seed=55)),
    ("5. Taxas 2x",                 run_sim(500, SQ_REALISTIC, fee_mult=2.0, seed=55)),
    ("7. Volatilidade 3x",          run_sim(500, SQ_REALISTIC, vol_mult=3.0, seed=55)),
    ("8. Capital metade",           run_sim(500, SQ_REALISTIC, capital_init=CAPITAL_INICIAL/2, seed=55)),
    ("10. 30 dias ruins",           run_sim(2520, SQ_REALISTIC, regime_seq=["trending_dn","high_vol","low_liq"], seed=11)),
]
print(f"\n  {'Teste':<30} {'Lucro%':>8}  {'DD%':>7}  {'PF':>6}  {'Veredicto'}")
print(f"  {'_'*30} {'_'*8}  {'_'*7}  {'_'*6}  {'_'*12}")
ok_count = 0
for name, r in tests:
    v = V(r)
    is_ok = "RUIM" not in v
    if is_ok: ok_count += 1
    print(f"  {name:<30} {r['lucro_pct']:>+7.1f}%  {r['max_dd']:>6.1f}%  {r['pf']:>6.3f}  {v}")

print(f"\n  Passou: {ok_count}/{len(tests)} testes")
if ok_count >= 7:
    print("  >>> SISTEMA ROBUSTO - pronto para capital real pequeno (sem alavancagem)")
elif ok_count >= 5:
    print("  >>> SISTEMA OK - rodar 30 dias paper antes de arriscar real")
elif ok_count >= 3:
    print("  >>> SISTEMA MARGINAL - precisa mais ajustes")
else:
    print("  >>> SISTEMA FRAGIL - nao usar capital real")

print(f"\n  NOTA IMPORTANTE:")
print(f"  Signal Quality breakeven = {breakeven_sq:.2f}" if breakeven_sq else "  SQ breakeven nao encontrado")
if breakeven_sq:
    print(f"  Para o bot ser viavel, seus sinais precisam ter")
    print(f"  pelo menos {breakeven_sq*100:.0f}% de poder preditivo.")
print(f"  Rode o bot em paper 30 dias e compare win rate real vs simulado.")
print(f"  Se WR real > 50% e PF real > 1.3 -> os sinais tem qualidade suficiente.")
print()
