"""Teste rápido para validar as melhorias nas engines v2."""
from app.engines.fvg import FVGAnalyzer
from app.engines.liquidity_sweep import LiquiditySweepAnalyzer
from app.engines.squeeze import SqueezeAnalyzer
import random

random.seed(42)

# ─── FVG: impulso de alta forte (>2x ATR) seguido de retração parcial ───
# Cenario real: tendencia de alta, impulso de 4%, preco atual dentro da zona
prices_fvg = [100.0]
for _ in range(65):   # base de tendência suave de alta
    prices_fvg.append(prices_fvg[-1] * (1 + random.uniform(-0.003, 0.006)))
# Cria impulso forte de exatamente o tipo que FVG detecta
prices_fvg += [prices_fvg[-1]]         # candle A  (base)
prices_fvg += [prices_fvg[-1] * 1.042] # candle B  (impulso +4.2%)
prices_fvg += [prices_fvg[-1] * 0.985] # candle C  (retração -1.5% = ainda acima de A)
for _ in range(9):                      # candles seguintes ainda dentro do gap
    prices_fvg.append(prices_fvg[-1] * (1 + random.uniform(-0.002, 0.002)))
volumes_fvg = [random.uniform(1500, 3000) for _ in range(len(prices_fvg) - 1)] + [8000.0]  # vol alto no impulso

print(f"FVG preco atual: {prices_fvg[-1]:.4f}  candle A: {prices_fvg[-12]:.4f}  candle B: {prices_fvg[-11]:.4f}")
r_fvg = FVGAnalyzer.calculate_fvg_score(prices_fvg, volumes_fvg)
print(f"FVG: score={r_fvg['fvg_score']:.4f}  valid={r_fvg['entry_valid']}  dir={r_fvg['direction']}")
print(f"     gap={r_fvg['gap_size_pct']:.2f}%  fill={r_fvg['fill_pct']:.1f}%")
print()

# ─── Liquidity Sweep: mercado lateral, preco varre suporte e volta ───
# Cenario: preco em range 101-103, varre para 100.3 (sweep de fundo), volta para 101.5
prices_ls = []
for i in range(70):   # ranging market
    prices_ls.append(101.5 + random.uniform(-1.0, 1.0))
# Janela de referencia (bars -15 a -5): range normal
for _ in range(5):
    prices_ls.append(101.8 + random.uniform(-0.3, 0.3))  # suporte ~101.5
# Sweep: barra -4 cai abaixo do suporte
prices_ls.append(100.2)   # prices[-4] sweep abaixo de 101.5
prices_ls.append(100.6)   # prices[-3] começa a recuperar
prices_ls.append(101.1)   # prices[-2] quase no suporte
prices_ls.append(101.7)   # prices[-1] acima do suporte = reversão confirmada
vols_ls = [2000.0] * len(prices_ls)
vols_ls[-4] = 8500.0  # volume alto no sweep

print(f"LS   suporte approx: ~101.5  sweep bar: {prices_ls[-4]:.2f}  current: {prices_ls[-1]:.2f}")
r_ls = LiquiditySweepAnalyzer.calculate_sweep_score(prices_ls, vols_ls)
print(f"LS:  score={r_ls['sweep_score']:.4f}  valid={r_ls['entry_valid']}  dir={r_ls['direction']}")
print(f"     swept_level={r_ls.get('swept_level', 0):.4f}  depth={r_ls.get('sweep_depth_pct', 0)*100:.3f}%")
print()

# ─── Squeeze: compressaõ + expansão direcional ───
# Cenario: 25 candles muito comprimidos, depois explode para cima
base_sq = 50.0
prices_sq = []
for _ in range(55):   # histórico normal
    prices_sq.append(base_sq + random.uniform(-0.8, 0.8))
for _ in range(25):   # squeeze: volatilidade muito baixa
    prices_sq.append(50.0 + random.uniform(-0.05, 0.05))
prices_sq.append(50.95)  # breakout! acima de BB superior
vols_sq = [random.uniform(1000, 4000) for _ in range(len(prices_sq))]
vols_sq[-1] = 10000.0  # volume alto no breakout

print(f"SQ   ultimo preco: {prices_sq[-1]:.2f}  (media: 50.0)")
r_sq = SqueezeAnalyzer.calculate_squeeze_score(prices_sq, vols_sq)
print(f"SQ:  score={r_sq['squeeze_score']:.4f}  valid={r_sq['entry_valid']}  dir={r_sq['direction']}")
print(f"     in_squeeze={r_sq['in_squeeze']}  kc={r_sq['kc_squeeze']}  bars={r_sq['squeeze_bars']}")
print()
print("Todos os engines v2 carregaram sem erros")
