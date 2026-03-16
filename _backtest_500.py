"""
Backtest WIN/WDO — Dez/2025 a Mar/2026
Capital inicial: R$500
5 estrategias testadas para encontrar o melhor encaixe.

ESTRATEGIAS:
  1. TREND-15  - segue direcao dos primeiros 15min
  2. FADE-15   - vai contra os primeiros 15min (mean reversion)
  3. ORB-30    - Opening Range Breakout (aguarda 30min, opera break)
  4. EMA-CROSS - cruza EMA9 x EMA21
  5. VWAP-REV  - reverte para VWAP quando preco se afasta >0.6%

WIN: 1 ponto = R$0.20 | WDO: 1 ponto = R$10.00
"""
import MetaTrader5 as mt5
from datetime import datetime

mt5.initialize(login=17727497, password='Po3LSz1@', server='XPMT5-PRD')

CAPITAL_INI = 500.0

CONFIGS = {
    'WINM26': {
        'ponto': 0.20,
        'margem': 118,
        'stops': {
            'TREND-15':  200,
            'FADE-15':   200,
            'ORB-30':    150,
            'EMA-CROSS': 250,
            'VWAP-REV':  180,
        },
        'rr': 2.0,
    },
    'WDOJ26': {
        'ponto': 10.0,
        'margem': 64,
        'stops': {
            'TREND-15':  10,
            'FADE-15':   10,
            'ORB-30':    8,
            'EMA-CROSS': 12,
            'VWAP-REV':  8,
        },
        'rr': 2.0,
    },
}


def calc_ema(series, period):
    k = 2.0 / (period + 1)
    e = series[0]
    result = [e]
    for v in series[1:]:
        e = v * k + e * (1 - k)
        result.append(e)
    return result


def calc_vwap(candles):
    cum_tpv = 0.0
    cum_vol = 0.0
    result = []
    for c in candles:
        tp = (c['high'] + c['low'] + c['close']) / 3.0
        v = max(c['tick_volume'], 1)
        cum_tpv += tp * v
        cum_vol += v
        result.append(cum_tpv / cum_vol)
    return result


def simula_op(candles, idx, direcao, stop_pts, alvo_pts):
    if idx >= len(candles) - 1:
        return 0.0, 'FIM'
    entrada = candles[idx]['close']
    stop_p = entrada - direcao * stop_pts
    alvo_p = entrada + direcao * alvo_pts
    fechamento = candles[-1]['close']
    resultado_pts = (fechamento - entrada) * direcao
    hit = 'FIM'
    for c in candles[idx + 1:]:
        if direcao == 1:
            if c['low'] <= stop_p:
                resultado_pts = -stop_pts
                hit = 'STOP'
                break
            if c['high'] >= alvo_p:
                resultado_pts = alvo_pts
                hit = 'ALVO'
                break
        else:
            if c['high'] >= stop_p:
                resultado_pts = -stop_pts
                hit = 'STOP'
                break
            if c['low'] <= alvo_p:
                resultado_pts = alvo_pts
                hit = 'ALVO'
                break
    return resultado_pts, hit


def backtest_estrategia(sym, estrategia):
    cfg = CONFIGS[sym]
    ponto = cfg['ponto']
    stop_pts = cfg['stops'][estrategia]
    alvo_pts = int(stop_pts * cfg['rr'])
    custo = stop_pts * ponto * 0.04

    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 5000)
    if rates is None or len(rates) == 0:
        return None

    dias = {}
    for r in rates:
        d = datetime.utcfromtimestamp(r['time']).strftime('%Y-%m-%d')
        if d not in dias:
            dias[d] = []
        dias[d].append(r)

    capital = CAPITAL_INI
    resultados = []

    for d, candles in sorted(dias.items()):
        if len(candles) < 30:
            continue
        if capital < cfg['margem'] * 1.5:
            break

        closes = [c['close'] for c in candles]
        direcao = 0
        entrada_idx = None

        if estrategia == 'TREND-15':
            abertura = candles[0]['open']
            conf = candles[3]['close']
            direcao = 1 if conf > abertura else -1
            entrada_idx = 3

        elif estrategia == 'FADE-15':
            abertura = candles[0]['open']
            conf = candles[3]['close']
            direcao = -1 if conf > abertura else 1
            entrada_idx = 3

        elif estrategia == 'ORB-30':
            orb_high = max(c['high'] for c in candles[:6])
            orb_low  = min(c['low']  for c in candles[:6])
            for i in range(6, len(candles)):
                if candles[i]['high'] > orb_high:
                    direcao = 1
                    entrada_idx = i
                    break
                if candles[i]['low'] < orb_low:
                    direcao = -1
                    entrada_idx = i
                    break

        elif estrategia == 'EMA-CROSS':
            ema9  = calc_ema(closes, 9)
            ema21 = calc_ema(closes, 21)
            for i in range(22, len(candles)):
                if ema9[i - 1] <= ema21[i - 1] and ema9[i] > ema21[i]:
                    direcao = 1
                    entrada_idx = i
                    break
                if ema9[i - 1] >= ema21[i - 1] and ema9[i] < ema21[i]:
                    direcao = -1
                    entrada_idx = i
                    break

        elif estrategia == 'VWAP-REV':
            vwaps = calc_vwap(candles)
            for i in range(6, len(candles) - 10):
                preco = closes[i]
                vw = vwaps[i]
                desvio = (preco - vw) / vw
                if desvio > 0.006:
                    direcao = -1
                    entrada_idx = i
                    break
                if desvio < -0.006:
                    direcao = 1
                    entrada_idx = i
                    break

        if entrada_idx is None or direcao == 0:
            continue

        resultado_pts, hit = simula_op(candles, entrada_idx, direcao, stop_pts, alvo_pts)
        resultado_brl = round(resultado_pts * ponto - custo, 2)
        capital += resultado_brl
        resultados.append({'d': d, 'pnl': resultado_brl, 'hit': hit, 'dir': direcao})

    if not resultados:
        return None

    wins   = sum(1 for r in resultados if r['pnl'] > 0)
    losses = len(resultados) - wins
    total  = len(resultados)
    wr     = round(wins / total * 100, 1)
    total_pnl = sum(r['pnl'] for r in resultados)
    ganhos = [r['pnl'] for r in resultados if r['pnl'] > 0]
    perdas = [r['pnl'] for r in resultados if r['pnl'] <= 0]
    avg_win  = round(sum(ganhos) / len(ganhos), 2) if ganhos else 0
    avg_loss = round(sum(perdas) / len(perdas), 2) if perdas else 0
    fator = round(abs((avg_win * wins) / (avg_loss * losses)), 2) if losses > 0 and avg_loss != 0 else 0

    pico = CAPITAL_INI
    cur  = CAPITAL_INI
    max_dd = 0.0
    for r in resultados:
        cur += r['pnl']
        if cur > pico:
            pico = cur
        dd = pico - cur
        if dd > max_dd:
            max_dd = dd

    max_seq = 0
    seq = 0
    for r in resultados:
        if r['pnl'] <= 0:
            seq += 1
            max_seq = max(max_seq, seq)
        else:
            seq = 0

    return {
        'sym': sym,
        'estrategia': estrategia,
        'total': total,
        'wins': wins,
        'losses': losses,
        'wr': wr,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'fator': fator,
        'total_pnl': round(total_pnl, 2),
        'capital_final': round(capital, 2),
        'retorno_pct': round((capital / CAPITAL_INI - 1) * 100, 1),
        'max_dd': round(max_dd, 2),
        'max_dd_pct': round(max_dd / CAPITAL_INI * 100, 1),
        'max_seq_loss': max_seq,
        'stop_pts': stop_pts,
        'alvo_pts': alvo_pts,
        'risco_op': round(stop_pts * CONFIGS[sym]['ponto'], 2),
        'resultados': resultados,
    }


# ====================================================
# EXECUTA TODAS AS ESTRATEGIAS
# ====================================================
estrategias = ['TREND-15', 'FADE-15', 'ORB-30', 'EMA-CROSS', 'VWAP-REV']
todos_resultados = []

for sym in ['WINM26', 'WDOJ26']:
    print()
    print("=" * 65)
    print("  " + sym + "  —  Capital R$500  |  Dez/25-Mar/26")
    print("=" * 65)
    print("  {:<12} {:>5} {:>6} {:>6} {:>8} {:>8} {:>7} {:>5} {:>7}".format(
        "Estrategia", "Ops", "WR%", "Fator", "P&L", "Capital", "DD%", "SeqL", "Stop"))
    print("  " + "-" * 62)

    sym_res = []
    for est in estrategias:
        r = backtest_estrategia(sym, est)
        if r:
            todos_resultados.append(r)
            sym_res.append(r)
            print("  {:<12} {:>5} {:>6} {:>6} {:>8} {:>8} {:>7} {:>5} {:>7}".format(
                est,
                r['total'],
                str(r['wr']) + "%",
                r['fator'],
                "R$" + str(r['total_pnl']),
                "R$" + str(r['capital_final']),
                str(r['max_dd_pct']) + "%",
                r['max_seq_loss'],
                str(r['stop_pts']) + "pts",
            ))

    # Melhor para este ativo (score: retorno - 0.5*drawdown)
    vivos = [r for r in sym_res if r['capital_final'] > CAPITAL_INI * 0.5]
    if vivos:
        melhor = max(vivos, key=lambda x: x['retorno_pct'] - 0.5 * x['max_dd_pct'])
        print()
        print("  >> MELHOR ESTRATEGIA: " + melhor['estrategia'])
        print("     Retorno: +" + str(melhor['retorno_pct']) + "%  |  Drawdown max: " + str(melhor['max_dd_pct']) + "%")
        print("     WR: " + str(melhor['wr']) + "%  |  Fator lucro: " + str(melhor['fator']))
        print("     Stop: " + str(melhor['stop_pts']) + " pts  |  Alvo: " + str(melhor['alvo_pts']) + " pts  |  Risco/op: R$" + str(melhor['risco_op']))
        print()
        print("  Ultimos 10 dias (" + melhor['estrategia'] + "):")
        for res in melhor['resultados'][-10:]:
            lado = "LONG " if res['dir'] == 1 else "SHORT"
            sinal = ("+" if res['pnl'] > 0 else "") + str(res['pnl'])
            print("    " + res['d'] + "  " + lado + "  " + res['hit'].ljust(4) + "  R$" + sinal)
    else:
        print()
        print("  >> ATENCAO: nenhuma estrategia preservou capital suficiente")
        print("     Recomendacao: aporte R$1.000+ antes de operar " + sym)

print()
print("=" * 65)
print("  RANKING FINAL — score = retorno% - 0.5*drawdown%")
print("=" * 65)
ranking = sorted(todos_resultados, key=lambda x: x['retorno_pct'] - 0.5 * x['max_dd_pct'], reverse=True)
for i, r in enumerate(ranking[:6], 1):
    score = round(r['retorno_pct'] - 0.5 * r['max_dd_pct'], 1)
    print("  " + str(i) + ". " + r['sym'] + " + " + r['estrategia'].ljust(12) +
          "  score=" + str(score) +
          "  ret=" + str(r['retorno_pct']) + "%" +
          "  dd=" + str(r['max_dd_pct']) + "%" +
          "  WR=" + str(r['wr']) + "%" +
          "  fator=" + str(r['fator']))

mt5.shutdown()
