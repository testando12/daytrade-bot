"""
MONTE CARLO ANALYSIS — Day Trade Bot
Teste de robustez estatística via 10.000 embaralhamentos dos trades históricos.

Métricas calculadas por simulação:
  • PnL final
  • Max Drawdown (%)
  • Ulcer Index (risco psicológico / suavidade da curva)
  • Sequência máxima de perdas consecutivas

Fonte dos dados: API local /performance
"""

import requests
import random
import math
import statistics
import json
from datetime import datetime

API    = "http://localhost:8000"
N_SIMS = 10_000
SEED   = None   # None = aleatório real; setar int para reprodutibilidade

SEP  = "=" * 68
sep  = "─" * 68

# ─────────────────────────────────────────────────────────────────────────────
# 1. COLETA DE DADOS
# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  MONTE CARLO — Day Trade Bot")
print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  {N_SIMS:,} simulações")
print(SEP)
print()

print("Buscando dados da API local...")
try:
    r = requests.get(f"{API}/performance", timeout=5)
    d = r.json()["data"]
    print("  [API] Dados carregados via API.\n")
except Exception:
    # Fallback: lê direto do JSON persistido
    import os
    json_path = os.path.join(os.path.dirname(__file__), "data", "performance.json")
    with open(json_path, encoding="utf-8") as f:
        raw = json.load(f)
    # Monta estrutura compatível com a resposta da API
    wins  = int(raw.get("win_count", 0))
    losses = int(raw.get("loss_count", 0))
    gain  = float(raw.get("total_gain", 0))
    loss  = float(raw.get("total_loss", 0))
    d = {
        "total_cycles":    wins + losses,
        "win_count":       wins,
        "loss_count":      losses,
        "total_gain":      gain,
        "total_loss":      loss,
        "total_pnl":       float(raw.get("total_pnl_offset", gain - loss)),
        "avg_pnl_per_cycle": (gain - loss) / max(wins + losses, 1),
        "current_capital": 3289.05,    # último capital conhecido
        "max_drawdown_pct": 3.81,
        "sharpe_ratio":    3.67,
        "recent_cycles":   raw.get("cycles", []),
    }
    print("  [OFFLINE] Dados carregados do JSON local.\n")

capital_inicial = float(d.get("current_capital", 3289.05))
win_count  = int(d["win_count"])
loss_count = int(d["loss_count"])
total_gain = float(d["total_gain"])
total_loss = float(d["total_loss"])
total_pnl  = float(d["total_pnl"])
cycles_raw = d.get("recent_cycles", [])   # últimos 20 (disponíveis)

avg_win  = total_gain / win_count  if win_count  else 0
avg_loss = total_loss / loss_count if loss_count else 0
wr       = win_count / (win_count + loss_count)
N        = win_count + loss_count

print(f"  Capital atual   : R${capital_inicial:,.2f}")
print(f"  Trades totais   : {N}  (wins={win_count}, losses={loss_count})")
print(f"  Win rate        : {wr*100:.1f}%")
print(f"  Avg win         : R${avg_win:.2f}  |  Avg loss : R${avg_loss:.2f}")
print(f"  Profit Factor   : {total_gain/max(total_loss,0.01):.3f}")
print(f"  Total PnL real  : R${total_pnl:.2f}")
print()

# ─────────────────────────────────────────────────────────────────────────────
# 2. RECONSTITUIÇÃO DA SEQUÊNCIA DE TRADES
#    Se temos o array de ciclos detalhados usa-os;
#    caso contrário, sintetiza a partir dos agregados.
# ─────────────────────────────────────────────────────────────────────────────
if len(cycles_raw) >= N * 0.8:
    # Ciclos detalhados disponíveis — usa PnLs reais
    trade_pnls = [float(c["pnl"]) for c in cycles_raw]
    print(f"Usando {len(trade_pnls)} PnLs reais dos ciclos.\n")
else:
    # Sintetiza: N trades com avg_win / avg_loss preservando contagens exatas
    # Usa desvio padrão estimado por resampling bootstrap dos últimos 20 se disponível
    if cycles_raw:
        wins_raw  = [float(c["pnl"]) for c in cycles_raw if c.get("pnl", 0) > 0]
        losses_raw = [abs(float(c["pnl"])) for c in cycles_raw if c.get("pnl", 0) < 0]
        std_win  = statistics.stdev(wins_raw)  if len(wins_raw)  >= 2 else avg_win  * 0.40
        std_loss = statistics.stdev(losses_raw) if len(losses_raw) >= 2 else avg_loss * 0.35
    else:
        std_win  = avg_win  * 0.40   # ±40% do avg_win
        std_loss = avg_loss * 0.35   # ±35% do avg_loss

    rng = random.Random(42)   # seed fixo só para síntese, não para as simulações
    trade_pnls = []
    for _ in range(win_count):
        v = rng.gauss(avg_win, std_win)
        trade_pnls.append(max(v, 0.01))    # ganho nunca negativo
    for _ in range(loss_count):
        v = rng.gauss(avg_loss, std_loss)
        trade_pnls.append(-max(v, 0.01))   # perda sempre negativa

    print(f"Sintetizando {N} trades a partir dos agregados (std_win={std_win:.2f}, std_loss={std_loss:.2f}).\n")

if SEED is not None:
    random.seed(SEED)

# ─────────────────────────────────────────────────────────────────────────────
# 3. FUNÇÕES DE MÉTRICAS
# ─────────────────────────────────────────────────────────────────────────────
def max_drawdown(equity_curve):
    """Retorna o max drawdown em % sobre o capital máximo atingido."""
    peak = equity_curve[0]
    dd   = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        drop = (peak - v) / peak * 100 if peak > 0 else 0
        if drop > dd:
            dd = drop
    return dd

def ulcer_index(equity_curve):
    """
    Ulcer Index = sqrt(mean(drawdown_i^2))
    Mede o risco psicológico — combina profundidade e duração dos drawdowns.
    Valores < 5% = excelente, 5-10% = aceitável, > 15% = alto risco.
    """
    peak = equity_curve[0]
    sq_sum = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        sq_sum += dd * dd
    return math.sqrt(sq_sum / len(equity_curve))

def max_consec_losses(pnl_seq):
    mx = cur = 0
    for p in pnl_seq:
        if p < 0:
            cur += 1
            mx = max(mx, cur)
        else:
            cur = 0
    return mx

def run_simulation(pnls, capital_start):
    shuffled = pnls[:]
    random.shuffle(shuffled)
    equity = [capital_start]
    for p in shuffled:
        equity.append(equity[-1] + p)
    return {
        "final_pnl":     equity[-1] - capital_start,
        "max_dd":        max_drawdown(equity),
        "ulcer":         ulcer_index(equity),
        "consec_losses": max_consec_losses(shuffled),
        "profitable":    equity[-1] > capital_start,
    }

# ─────────────────────────────────────────────────────────────────────────────
# 4. EXECUÇÃO DAS SIMULAÇÕES
# ─────────────────────────────────────────────────────────────────────────────
print(f"Executando {N_SIMS:,} simulações Monte Carlo...")
results = []
for i in range(N_SIMS):
    results.append(run_simulation(trade_pnls, capital_inicial))

print("Concluído.\n")

# ─────────────────────────────────────────────────────────────────────────────
# 5. ANÁLISE DOS RESULTADOS
# ─────────────────────────────────────────────────────────────────────────────
final_pnls      = [r["final_pnl"]     for r in results]
max_dds         = [r["max_dd"]        for r in results]
ulcers          = [r["ulcer"]         for r in results]
consec_losses   = [r["consec_losses"] for r in results]
profitable_pct  = sum(r["profitable"] for r in results) / N_SIMS * 100

def pct(data, p): return sorted(data)[int(len(data) * p / 100)]

print(SEP)
print("  1. DISTRIBUIÇÃO DE PnL FINAL  (base: capital atual)")
print(SEP)
print(f"  Resultado REAL (sequência original) : R${total_pnl:+,.2f}")
print(f"  Média das simulações                : R${statistics.mean(final_pnls):+,.2f}")
print(f"  Mediana                             : R${statistics.median(final_pnls):+,.2f}")
print(f"  Desvio padrão                       : R${statistics.stdev(final_pnls):,.2f}")
print()
print(f"  ← Pior  1% (VaR-99)  : R${pct(final_pnls, 1):+,.2f}")
print(f"  ← Pior  5% (VaR-95)  : R${pct(final_pnls, 5):+,.2f}")
print(f"  ← Pior 10%            : R${pct(final_pnls,10):+,.2f}")
print(f"  ─ Mediana             : R${pct(final_pnls,50):+,.2f}")
print(f"  → Melhor 10%          : R${pct(final_pnls,90):+,.2f}")
print(f"  → Melhor  5%          : R${pct(final_pnls,95):+,.2f}")
print(f"  → Melhor  1%          : R${pct(final_pnls,99):+,.2f}")
print()
print(f"  Simulações LUCRATIVAS : {profitable_pct:.1f}%  ({sum(r['profitable'] for r in results):,}/{N_SIMS:,})")
verdict_pnl = "✅ ROBUSTA" if profitable_pct >= 90 else ("⚠️  MODERADA" if profitable_pct >= 75 else "❌ FRÁGIL")
print(f"  Veredito de lucratividade : {verdict_pnl}")
print()

print(SEP)
print("  2. MAX DRAWDOWN (por embaralhamento)")
print(SEP)
print(f"  Max DD REAL (sequência original)    : ~{abs(float(d.get('max_drawdown_pct', 0))):.2f}%")
print(f"  Média  MC                           : {statistics.mean(max_dds):.2f}%")
print(f"  Mediana MC                          : {statistics.median(max_dds):.2f}%")
print()
print(f"  Melhor  5% (DD mínimo)  : {pct(max_dds, 5):.2f}%")
print(f"  Mediana                 : {pct(max_dds,50):.2f}%")
print(f"  Pior   90%              : {pct(max_dds,90):.2f}%")
print(f"  Pior   95%              : {pct(max_dds,95):.2f}%")
print(f"  Pior   99% (worst case) : {pct(max_dds,99):.2f}%")
dd_99 = pct(max_dds, 99)
verdict_dd = "✅ CONTROLADO" if dd_99 <= 15 else ("⚠️  MODERADO" if dd_99 <= 25 else "❌ ALTO RISCO")
print(f"  Veredito de drawdown (99th pct)     : {verdict_dd}")
print()

print(SEP)
print("  3. ULCER INDEX  (risco psicológico / suavidade da curva)")
print(SEP)
print("  Referência: < 5% = excelente | 5–10% = aceitável | > 15% = alto")
print()
print(f"  Média  : {statistics.mean(ulcers):.2f}%")
print(f"  Mediana: {statistics.median(ulcers):.2f}%")
print(f"  P90    : {pct(ulcers,90):.2f}%")
print(f"  P95    : {pct(ulcers,95):.2f}%")
ui_median = statistics.median(ulcers)
verdict_ui = "✅ EXCELENTE" if ui_median < 5 else ("✅ BOM" if ui_median < 10 else ("⚠️  ACEITÁVEL" if ui_median < 15 else "❌ ALTO"))
print(f"  Veredito Ulcer Index    : {verdict_ui}")
print()

print(SEP)
print("  4. SEQUÊNCIAS DE PERDAS CONSECUTIVAS")
print(SEP)
lmax_teoria = math.log(N) / math.log(1 / (1 - wr)) if wr < 1 else 0
print(f"  Sequência máxima TEÓRICA (fórmula ln): {lmax_teoria:.1f} perdas")
print(f"  Média   nas simulações   : {statistics.mean(consec_losses):.1f}")
print(f"  Mediana                  : {statistics.median(consec_losses):.1f}")
print(f"  P90 (normal)             : {pct(consec_losses,90)}")
print(f"  P99 (extremo)            : {pct(consec_losses,99)}")
cl_p99 = pct(consec_losses, 99)
max_dd_consec = cl_p99 * avg_loss
print(f"  Drawdown de {cl_p99} perdas seguidas: R${max_dd_consec:.2f}  "
      f"({max_dd_consec/capital_inicial*100:.2f}% do capital)")
print()

print(SEP)
print("  5. EXPECTANCY & COMPARATIVO")
print(SEP)
expectancy = (wr * avg_win) - ((1 - wr) * avg_loss)
breakeven_wr = avg_loss / (avg_win + avg_loss)
print(f"  Expectancy por trade    : R${expectancy:+.4f}")
print(f"  Win rate breakeven      : {breakeven_wr*100:.1f}%  (atual: {wr*100:.1f}% — margem: +{(wr-breakeven_wr)*100:.1f}pp)")
print(f"  Profit Factor líquido   : {total_gain/max(total_loss,0.01):.3f}")
print()

# ─────────────────────────────────────────────────────────────────────────────
# 6. VEREDITO FINAL
# ─────────────────────────────────────────────────────────────────────────────
print(SEP)
print("  VEREDITO FINAL")
print(SEP)

rubrica = [
    ("Lucratividade MC (≥90%)",         profitable_pct >= 90,  f"{profitable_pct:.1f}%"),
    ("Drawdown P99 controlado (≤15%)",   dd_99 <= 15,           f"{dd_99:.2f}%"),
    ("Ulcer Index mediana (≤10%)",       ui_median <= 10,       f"{ui_median:.2f}%"),
    ("Expectancy positiva",              expectancy > 0,        f"R${expectancy:+.4f}"),
    ("Profit Factor (≥1.8)",            total_gain/max(total_loss,0.01) >= 1.8, f"{total_gain/max(total_loss,0.01):.3f}"),
    ("Margem sobre breakeven (≥5pp)",    (wr - breakeven_wr) * 100 >= 5,  f"+{(wr-breakeven_wr)*100:.1f}pp"),
]

passed = 0
for label, ok, val in rubrica:
    status = "✅" if ok else "❌"
    if ok: passed += 1
    print(f"  {status}  {label:<42} {val}")

score = passed / len(rubrica) * 10
print()
print(f"  Score Monte Carlo : {score:.1f} / 10  ({passed}/{len(rubrica)} critérios)")

if score >= 9:
    conclusao = "Edge real altamente provável. Estratégia robusta a ordem aleatória dos trades."
elif score >= 7:
    conclusao = "Edge consistente. Poucos pontos a melhorar antes de escalar capital."
elif score >= 5:
    conclusao = "Edge moderado. Revisar drawdown ou expectancy antes de go-live."
else:
    conclusao = "Fragilidade detectada. Revisar estratégia antes de operar capital real."

print(f"  Conclusão         : {conclusao}")
print(SEP)
