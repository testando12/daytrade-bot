# -*- coding: utf-8 -*-
"""
Comparativo ORB-30 WIN: 1 trade/dia vs 2 trades/dia
Capital R$500 | Stop 150pts | Alvo 300pts
"""
import MetaTrader5 as mt5
from datetime import datetime

mt5.initialize(login=17727497, password='Po3LSz1@', server='XPMT5-PRD')

SYM    = 'WINM26'
PONTO  = 0.20
STOP   = 150
ALVO   = 300
CAP_INI = 500.0
CUSTO  = round(STOP * PONTO * 0.04, 2)   # R$1.20 por operacao
ORB_N  = 6                                # 6 candles M5 = 30min range

rates = mt5.copy_rates_from_pos(SYM, mt5.TIMEFRAME_M5, 0, 5000)

dias = {}
for r in rates:
    d = datetime.utcfromtimestamp(r['time']).strftime('%Y-%m-%d')
    dias.setdefault(d, []).append(r)


def simula_trade(candles, start_idx, direcao):
    """Simula 1 trade a partir de start_idx. Retorna (resultado_pts, hit, next_idx)."""
    entrada = candles[start_idx]['close']
    stop_p  = entrada - direcao * STOP
    alvo_p  = entrada + direcao * ALVO
    res_pts = (candles[-1]['close'] - entrada) * direcao
    hit     = 'FIM'
    end_idx = len(candles) - 1
    for i, c in enumerate(candles[start_idx + 1:], start_idx + 1):
        if direcao == 1:
            if c['low']  <= stop_p: res_pts = -STOP; hit = 'STOP'; end_idx = i; break
            if c['high'] >= alvo_p: res_pts =  ALVO; hit = 'ALVO'; end_idx = i; break
        else:
            if c['high'] >= stop_p: res_pts = -STOP; hit = 'STOP'; end_idx = i; break
            if c['low']  <= alvo_p: res_pts =  ALVO; hit = 'ALVO'; end_idx = i; break
    pnl = round(res_pts * PONTO - CUSTO, 2)
    return pnl, hit, end_idx


def backtest(max_trades_dia):
    capital = CAP_INI
    ops = []

    for d, candles in sorted(dias.items()):
        if len(candles) < ORB_N + 2:
            continue
        if capital < 200:
            break

        # --- Monta ORB ---
        orb_high = max(c['high'] for c in candles[:ORB_N])
        orb_low  = min(c['low']  for c in candles[:ORB_N])

        trades_dia = 0
        idx = ORB_N

        while idx < len(candles) - 1 and trades_dia < max_trades_dia:
            preco = candles[idx]['close']

            if preco > orb_high:
                direcao = 1
            elif preco < orb_low:
                direcao = -1
            else:
                idx += 1
                continue

            pnl, hit, next_idx = simula_trade(candles, idx, direcao)
            capital += pnl
            ops.append({'d': d, 'pnl': pnl, 'hit': hit, 'dir': direcao})
            trades_dia += 1

            # Regra 2o trade: so re-entra se levou STOP (nao apos alvo)
            if hit == 'ALVO':
                break

            # Continua procurando proximo sinal apos fechar o trade
            idx = next_idx + 1

    return capital, ops


print()
print("=" * 60)
print("  COMPARATIVO: 1 trade/dia  vs  2 trades/dia  |  WIN ORB-30")
print("  Capital R$500  |  Stop 150pts (R$30)  |  Alvo 300pts (R$60)")
print("=" * 60)

for max_t in [1, 2]:
    cap_final, ops = backtest(max_t)

    total  = len(ops)
    wins   = sum(1 for o in ops if o['pnl'] > 0)
    losses = total - wins
    wr     = round(wins / total * 100, 1) if total > 0 else 0
    pnl_t  = sum(o['pnl'] for o in ops)
    ganhos = [o['pnl'] for o in ops if o['pnl'] > 0]
    perdas = [o['pnl'] for o in ops if o['pnl'] <= 0]
    avg_w  = round(sum(ganhos) / len(ganhos), 2) if ganhos else 0
    avg_l  = round(sum(perdas) / len(perdas), 2) if perdas else 0
    fator  = round(abs((avg_w * wins) / (avg_l * losses)), 2) if losses > 0 and avg_l != 0 else 0

    pico = CAP_INI; cur = CAP_INI; max_dd = 0.0
    for o in ops:
        cur += o['pnl']
        if cur > pico: pico = cur
        dd = pico - cur
        if dd > max_dd: max_dd = dd

    seq = max_seq = 0
    for o in ops:
        if o['pnl'] <= 0: seq += 1; max_seq = max(max_seq, seq)
        else: seq = 0

    risco_max_dia = round(31.20 * max_t, 2)

    print()
    print(f"  MAX {max_t} TRADE(S)/DIA:")
    print(f"    Operacoes totais  : {total}")
    print(f"    Win Rate          : {wr}%")
    print(f"    Fator lucro       : {fator}")
    print(f"    Ganho medio/op    : R${avg_w}")
    print(f"    Perda media/op    : R${avg_l}")
    print(f"    P&L total         : R${round(pnl_t,2):+.2f}")
    print(f"    Capital final     : R${round(cap_final,2):.2f}")
    print(f"    Retorno           : {round((cap_final/CAP_INI-1)*100,1)}%")
    print(f"    Drawdown max      : R${round(max_dd,2):.2f}  ({round(max_dd/CAP_INI*100,1)}% do capital)")
    print(f"    Seq. perdas max   : {max_seq}")
    print(f"    Risco maximo/dia  : R${risco_max_dia}  ({round(risco_max_dia/CAP_INI*100,1)}% do capital)")

print()
print("=" * 60)
mt5.shutdown()
