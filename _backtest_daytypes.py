"""
Backtest por Tipo de Dia - Day Type Strategy Optimizer

Responde a pergunta: "qual estratégia funciona melhor em cada tipo de dia?"

5 tipos de dia (literatura clássica + adaptações para futuros B3):
  -----------------------------------------------------------------
  1. TREND_UP      - Dia de tendência de alta forte
     Características: ORB rompido pra cima, movimento > 1x range do ORB,
                      >65% candles bullish, gap positivo frequente
     Literatura: "Trend days" de Steidlmayer, "Directional days" de Dalton
     Melhor estratégia: ORB-30 breakout LONG, Momentum seguidor

  2. TREND_DOWN     - Dia de tendência de baixa forte
     Características: inversão do TREND_UP
     Melhor estratégia: ORB-30 breakout SHORT, VWAP Reversal (fade no pico)

  3. RANGE_NORMAL   - Dia lateral / balanceado
     Características: preço oscila ao redor do VWAP/ORB, range < 0.5x ORB,
                      sem direcionalidade, >50% candles fecham perto do meio
     Literatura: "Balanced days" de Tom Hougaard / "Value area" de Dalton
     Melhor estratégia: Range Scalp ORB borders, VWAP Reversal, Fade ORB

  4. RANGE_WIDE     - Dia volátil / wide range (sem tendência)
     Características: range > 2x do range médio histórico, mas sem direcionalidade
                      consistente (oscila pra cima e pra baixo com força)
     Literatura: "Volatile balance" de SMB Capital
     Melhor estratégia: VWAP Reversal, Fade com stops maiores, SKIP preferível

  5. GAP_REVERSAL   - Gap na abertura que reverte
     Características: abertura > 0.5% fora do range anterior, reversão
                      detectada nas primeiras 30 minutes
     Literatura: "Gap fills" - estatisticamente ~73% dos gaps intraday fecham
     Melhor estratégia: Fade do Gap (entrar contra o gap logo após ORB)

Metodologia do backtest:
  1. Baixa candles M5 de WINM26/WINJ26 via MT5 (4 meses = ~85 pregões)
  2. Para cada pregão:
     a) Classifica o tipo do dia (sem lookahead - usa só primeiros 20min)
     b) Simula cada estratégia naquele dia com regras realistas
     c) Registra P&L, WR, e resultado por tipo de dia
  3. Gera tabela cruzada: estratégia × tipo de dia -> WR%, PF, Avg P&L

Estratégias testadas:
  A. ORB-30 BREAKOUT  - espera 30min, opera rompimento (atual)
  B. ORB-FADE         - espera breakout falso, fade de volta ao ORB (atual)
  C. RANGE-SCALP      - bounce nas bordas do ORB (atual)
  D. VWAP-REVERSION   - desvio da VWAP > threshold -> reverter (atual)
  E. TREND-FOLLOW     - após ORB + confirmação, segue tendência sem limite (novo)
  F. MOMENTUM-BREAK   - EMA9/21 cross após ORB -> segue (novo)

Custo por trade: 4% do risco (spread ~2pts WIN + corretagem XP)
Margem WIN: R$118/contrato (arredondado para R$120 seguro)
"""

import os
import sys
from datetime import datetime, date, timezone, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple, Optional

# --- Adiciona o diretório raiz ao path ----------------------------------------
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_BRT = timezone(timedelta(hours=-3))

# --- Parâmetros ---------------------------------------------------------------
SYMBOL       = os.getenv("WIN_SYMBOL", "WINJ26")
WIN_PONTO    = 0.20      # R$/ponto/contrato
CUSTO_PCT    = 0.04      # 4% do risco
LOOKBACK     = 5000      # candles M5 (~4 meses de pregões)
CAPITAL_INI  = 500.0

# Parâmetros das estratégias
PARAMS = {
    "ORB30":       {"stop": 150, "target": 300, "rr": 2.0},
    "ORB_FADE":    {"stop": 120, "target": 120, "rr": 1.0},
    "RANGE_SCALP": {"stop":  80, "target": 140, "rr": 1.75},
    "VWAP_REV":    {"stop":  80, "target": 140, "rr": 1.75},   # adaptado para WIN
    "TREND_FOLLOW":{"stop": 200, "target": 500, "rr": 2.5},    # novo - trend days
    "MOMENTUM":    {"stop": 200, "target": 400, "rr": 2.0},    # novo - EMA9/21
}

# Thresholds do Day Classifier (baseados em análise histórica WIN B3)
ORB_CANDLES          = 6      # 09:00-09:29 (6 candles M5)
TREND_MOMENTUM_PCT   = 0.50   # >50% do range = possível trend
STRONG_MOMENTUM_PCT  = 1.00   # >100% = trend forte (sem lookahead na classificação)
CONSISTENCY_MIN      = 0.60   # >=60% candles na mesma direção
GAP_PCT              = 0.003  # gap > 0.3% do preço anterior = gap day
WIDE_RANGE_MULT      = 2.0    # range > 2x média histórica = wide range day


# --- Conexão MT5 --------------------------------------------------------------
def _connect_mt5():
    try:
        import MetaTrader5 as mt5
        login    = os.getenv("MT5_LOGIN", "").strip()
        password = os.getenv("MT5_PASSWORD", "").strip()
        server   = os.getenv("MT5_SERVER", "").strip()
        if login and password and server:
            ok = mt5.initialize(login=int(login), password=password, server=server)
        else:
            ok = mt5.initialize()
        if ok:
            print(f"[backtest] MT5 conectado - conta: {mt5.account_info().login if mt5.account_info() else '?'}")
            return mt5
        print(f"[backtest] MT5 falhou: {mt5.last_error()}")
    except ImportError:
        print("[backtest] MetaTrader5 não disponível")
    return None


def _fetch_candles(mt5, symbol: str, n: int) -> List[dict]:
    """Baixa n candles M5 e retorna como lista de dicts com datetime BRT."""
    if not mt5.symbol_select(symbol, True):
        print(f"[backtest] Símbolo {symbol} não encontrado")
        return []
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n)
    if rates is None or len(rates) == 0:
        print(f"[backtest] Nenhum candle retornado para {symbol}")
        return []
    result = []
    for r in rates:
        t = datetime.utcfromtimestamp(r["time"]).replace(tzinfo=_BRT)
        result.append({
            "time":  t,
            "open":  float(r["open"]),
            "high":  float(r["high"]),
            "low":   float(r["low"]),
            "close": float(r["close"]),
            "vol":   float(r["tick_volume"]),
        })
    return result


def _split_by_day(candles: List[dict]) -> Dict[date, List[dict]]:
    """Agrupa candles por dia de trading e filtra horário de mercado (09:00-17:55)."""
    days: Dict[date, List[dict]] = defaultdict(list)
    for c in candles:
        t = c["time"]
        # Filtra só horário de mercado B3 WIN
        if t.hour < 9 or (t.hour == 17 and t.minute >= 55) or t.hour >= 18:
            continue
        # Ignora candles de fim de semana
        if t.weekday() >= 5:
            continue
        days[t.date()].append(c)
    # Filtra dias com poucos candles (pregão incompleto)
    return {d: cs for d, cs in days.items() if len(cs) >= 30}


# --- Classificador de Tipo de Dia ---------------------------------------------

def classify_day(day_candles: List[dict], avg_range: float) -> Tuple[str, dict]:
    """
    Classifica o tipo do dia usando APENAS os primeiros ~20min pós-abertura
    (sem lookahead bias).

    Retorna (day_type, metrics_dict)
    """
    orb = [c for c in day_candles if c["time"].hour == 9 and c["time"].minute < 30]
    post_orb_early = [c for c in day_candles
                      if (c["time"].hour == 9 and c["time"].minute >= 30)
                      or (c["time"].hour == 10 and c["time"].minute < 0)]  # 09:30-09:59

    # Usa os primeiros 4 candles pós-ORB para classificar (20min) -> sem lookahead
    post_orb_sample = [c for c in day_candles
                       if c["time"].hour == 9 and c["time"].minute >= 30][:4]

    if len(orb) < ORB_CANDLES:
        return "UNDEFINED", {}

    orb_high = max(c["high"] for c in orb)
    orb_low  = min(c["low"]  for c in orb)
    orb_range = orb_high - orb_low
    if orb_range <= 0:
        return "UNDEFINED", {}

    # -- Detecta Gap de abertura ----------------------------------------------
    open_price = day_candles[0]["open"]

    # -- Wide Range Day: range do ORB > 2x média histórica? ------------------
    is_wide_range = avg_range > 0 and orb_range > avg_range * WIDE_RANGE_MULT

    # -- Sem candles pós-ORB ainda (primeiros ciclos do dia) ------------------
    if len(post_orb_sample) < 2:
        return "AWAITING", {"orb_high": orb_high, "orb_low": orb_low, "orb_range": orb_range}

    # -- Momentum com os primeiros 4 candles pós-ORB --------------------------
    first_open  = post_orb_sample[0]["open"]
    last_close  = post_orb_sample[-1]["close"]
    momentum    = last_close - first_open
    mom_ratio   = momentum / orb_range if orb_range > 0 else 0.0

    n = len(post_orb_sample)
    bull_count = sum(1 for c in post_orb_sample if c["close"] >= c["open"])
    bear_count = n - bull_count
    consist_up   = bull_count / n
    consist_down = bear_count / n

    metrics = {
        "orb_high":    orb_high,
        "orb_low":     orb_low,
        "orb_range":   orb_range,
        "momentum":    momentum,
        "mom_ratio":   mom_ratio,
        "consist_up":  consist_up,
        "consist_down": consist_down,
        "is_wide_range": is_wide_range,
    }

    # -- Classifica -----------------------------------------------------------
    if is_wide_range:
        return "RANGE_WIDE", metrics

    if mom_ratio > TREND_MOMENTUM_PCT and consist_up >= CONSISTENCY_MIN:
        return "TREND_UP", metrics

    if mom_ratio < -TREND_MOMENTUM_PCT and consist_down >= CONSISTENCY_MIN:
        return "TREND_DOWN", metrics

    return "RANGE_NORMAL", metrics


# --- Simulador de Operações ---------------------------------------------------

def _simula(candles: List[dict], idx: int, direcao: int, stop_pts: float,
            target_pts: float) -> Tuple[float, str]:
    """
    Simula uma operação saindo de idx, com stop e alvo em pontos.
    Retorna (resultado_pts, hit: "ALVO"|"STOP"|"FIM")
    """
    if idx >= len(candles) - 1:
        return 0.0, "FIM"
    entrada = candles[idx]["close"]
    stop_p  = entrada - direcao * stop_pts
    alvo_p  = entrada + direcao * target_pts
    eod_price = candles[-1]["close"]

    for c in candles[idx + 1:]:
        if direcao == 1:
            if c["low"] <= stop_p:
                return -stop_pts, "STOP"
            if c["high"] >= alvo_p:
                return target_pts, "ALVO"
        else:
            if c["high"] >= stop_p:
                return -stop_pts, "STOP"
            if c["low"] <= alvo_p:
                return target_pts, "ALVO"

    # EOD - fecha no fechamento do dia
    resultado = (eod_price - entrada) * direcao
    return resultado, "FIM"


def _custo(stop_pts: float) -> float:
    return round(stop_pts * WIN_PONTO * CUSTO_PCT, 2)


# --- Estratégias --------------------------------------------------------------

def strat_orb30(day_candles: List[dict], metrics: dict) -> Optional[Tuple[float, str, int]]:
    """ORB-30: aguarda rompimento, opera breakout."""
    p = PARAMS["ORB30"]
    orb_h = metrics.get("orb_high")
    orb_l = metrics.get("orb_low")
    if not orb_h:
        return None
    for i, c in enumerate(day_candles):
        if c["time"].hour < 9 or (c["time"].hour == 9 and c["time"].minute < 30):
            continue
        if c["time"].hour > 14:  # janela até 14h
            break
        preco = c["close"]
        if preco > orb_h:
            pts, hit = _simula(day_candles, i, 1, p["stop"], p["target"])
            return pts, hit, i
        if preco < orb_l:
            pts, hit = _simula(day_candles, i, -1, p["stop"], p["target"])
            return pts, hit, i
    return None


def strat_orb_fade(day_candles: List[dict], metrics: dict) -> Optional[Tuple[float, str, int]]:
    """
    ORB-Fade: detecta primeiro breakout + rejeição (fecha de volta dentro do ORB).
    """
    p = PARAMS["ORB_FADE"]
    orb_h = metrics.get("orb_high")
    orb_l = metrics.get("orb_low")
    if not orb_h:
        return None

    breakout_dir    = None
    breakout_extreme = None
    confirm_count   = 0

    for i, c in enumerate(day_candles):
        if c["time"].hour < 9 or (c["time"].hour == 9 and c["time"].minute < 30):
            continue
        if c["time"].hour >= 15:
            break

        if breakout_dir is None:
            if c["high"] > orb_h:
                breakout_dir = "UP"
                breakout_extreme = c["high"]
            elif c["low"] < orb_l:
                breakout_dir = "DOWN"
                breakout_extreme = c["low"]
        else:
            # Rastreia: fechamento voltou para dentro do ORB?
            if breakout_dir == "UP" and c["close"] < orb_h:
                confirm_count += 1
                if confirm_count >= 2:
                    # Fade: SHORT (breakout pra cima que voltou)
                    stop_pts = min(p["stop"], breakout_extreme - orb_h + 30)
                    stop_pts = max(stop_pts, 40)
                    pts, hit = _simula(day_candles, i, -1, stop_pts, p["target"])
                    return pts, hit, i
            elif breakout_dir == "DOWN" and c["close"] > orb_l:
                confirm_count += 1
                if confirm_count >= 2:
                    # Fade: LONG (breakout pra baixo que voltou)
                    stop_pts = min(p["stop"], orb_l - breakout_extreme + 30)
                    stop_pts = max(stop_pts, 40)
                    pts, hit = _simula(day_candles, i, 1, stop_pts, p["target"])
                    return pts, hit, i
            else:
                confirm_count = 0  # reset se não reverteu

    return None


def strat_range_scalp(day_candles: List[dict], metrics: dict) -> Optional[Tuple[float, str, int]]:
    """Range Scalp: entra oposto nas bordas do ORB (bounce)."""
    p = PARAMS["RANGE_SCALP"]
    orb_h = metrics.get("orb_high")
    orb_l = metrics.get("orb_low")
    if not orb_h:
        return None
    ENTRY_BUFFER = 25   # pts da borda para considerar "perto"
    CONFIRM = 2

    confirm_up = confirm_down = 0
    for i, c in enumerate(day_candles):
        if c["time"].hour < 9 or (c["time"].hour == 9 and c["time"].minute < 30):
            continue
        if c["time"].hour >= 13:
            break
        preco = c["close"]
        # Perto do topo -> SHORT (rejeição do high)
        if preco >= orb_h - ENTRY_BUFFER:
            confirm_up += 1
            if confirm_up >= CONFIRM:
                pts, hit = _simula(day_candles, i, -1, p["stop"], p["target"])
                return pts, hit, i
        else:
            confirm_up = 0
        # Perto do fundo -> LONG (rejeição do low)
        if preco <= orb_l + ENTRY_BUFFER:
            confirm_down += 1
            if confirm_down >= CONFIRM:
                pts, hit = _simula(day_candles, i, 1, p["stop"], p["target"])
                return pts, hit, i
        else:
            confirm_down = 0
    return None


def strat_vwap_rev(day_candles: List[dict], metrics: dict) -> Optional[Tuple[float, str, int]]:
    """VWAP Reversal: desvio > threshold -> reverte."""
    p = PARAMS["VWAP_REV"]
    orb_range = metrics.get("orb_range", 500)
    DEV_MIN = max(80, orb_range * 0.15)   # threshold dinâmico baseado no range do dia

    cum_pv = cum_vol = 0.0
    prev_dev = 0.0

    for i, c in enumerate(day_candles):
        t = c["time"]
        if t.hour < 10 or t.hour >= 14:
            continue
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        vol = max(c["vol"], 1)
        cum_pv += tp * vol
        cum_vol += vol
        vwap = cum_pv / cum_vol
        dev = c["close"] - vwap
        # Reversão confirmada: desvio estava alto, agora diminuindo
        if abs(prev_dev) > DEV_MIN and abs(dev) < abs(prev_dev):
            direcao = -1 if prev_dev > 0 else 1   # reverte em direção ao VWAP
            pts, hit = _simula(day_candles, i, direcao, p["stop"], p["target"])
            return pts, hit, i
        prev_dev = dev
    return None


def strat_trend_follow(day_candles: List[dict], metrics: dict) -> Optional[Tuple[float, str, int]]:
    """
    Trend Follow (NOVO): confirma direcionalidade via 3 candles pós-ORB,
    entra na direção e usa alvo amplo (500pts).
    Ideal para TREND_UP / TREND_DOWN days.
    """
    p = PARAMS["TREND_FOLLOW"]
    orb_h = metrics.get("orb_high")
    orb_l = metrics.get("orb_low")
    orb_range = metrics.get("orb_range", 500)
    if not orb_h:
        return None

    confirm_up = confirm_down = 0
    for i, c in enumerate(day_candles):
        t = c["time"]
        if t.hour < 9 or (t.hour == 9 and t.minute < 30):
            continue
        if t.hour >= 13:
            break
        if c["close"] > c["open"]:
            confirm_up += 1
            confirm_down = 0
        else:
            confirm_down += 1
            confirm_up = 0

        # Confirma tendência com 3 candles na mesma direção + breakout do ORB
        if confirm_up >= 3 and c["close"] > orb_h:
            pts, hit = _simula(day_candles, i, 1, p["stop"], p["target"])
            return pts, hit, i
        if confirm_down >= 3 and c["close"] < orb_l:
            pts, hit = _simula(day_candles, i, -1, p["stop"], p["target"])
            return pts, hit, i
    return None


def strat_momentum(day_candles: List[dict], metrics: dict) -> Optional[Tuple[float, str, int]]:
    """
    EMA9/21 Cross após ORB: sinal de momentum via cruzamento de EMAs.
    Fallback para dias sem direcionalidade clara.
    """
    p = PARAMS["MOMENTUM"]
    closes = [c["close"] for c in day_candles]

    def _ema(series, period):
        k = 2.0 / (period + 1)
        e = series[0]
        result = [e]
        for v in series[1:]:
            e = v * k + e * (1 - k)
            result.append(e)
        return result

    ema9  = _ema(closes, 9)
    ema21 = _ema(closes, 21)

    for i in range(22, len(day_candles)):
        t = day_candles[i]["time"]
        if t.hour < 9 or (t.hour == 9 and t.minute < 30):
            continue
        if t.hour >= 13:
            break
        if ema9[i-1] <= ema21[i-1] and ema9[i] > ema21[i]:
            pts, hit = _simula(day_candles, i, 1, p["stop"], p["target"])
            return pts, hit, i
        if ema9[i-1] >= ema21[i-1] and ema9[i] < ema21[i]:
            pts, hit = _simula(day_candles, i, -1, p["stop"], p["target"])
            return pts, hit, i
    return None


# --- Motor do Backtest --------------------------------------------------------

STRATEGIES = {
    "ORB30":        strat_orb30,
    "ORB_FADE":     strat_orb_fade,
    "RANGE_SCALP":  strat_range_scalp,
    "VWAP_REV":     strat_vwap_rev,
    "TREND_FOLLOW": strat_trend_follow,
    "MOMENTUM":     strat_momentum,
}

DAY_TYPES = ["TREND_UP", "TREND_DOWN", "RANGE_NORMAL", "RANGE_WIDE", "UNDEFINED"]


def run_backtest(candles_all: List[dict]) -> dict:
    """
    Executa o backtest completo e retorna resultados por tipo de dia × estratégia.
    """
    days = _split_by_day(candles_all)
    print(f"\n[backtest] Total de pregões analisados: {len(days)}")

    # Calcula range médio histórico dos ORBs (para detectar wide range days)
    orb_ranges = []
    for day_candles in days.values():
        orb = [c for c in day_candles if c["time"].hour == 9 and c["time"].minute < 30]
        if len(orb) >= ORB_CANDLES:
            orb_ranges.append(max(c["high"] for c in orb) - min(c["low"] for c in orb))
    avg_orb_range = sum(orb_ranges) / len(orb_ranges) if orb_ranges else 500.0
    print(f"[backtest] Range médio do ORB: {avg_orb_range:.0f} pts")

    # Resultado: {day_type: {strategy: [pnl, ...]}}
    results: Dict[str, Dict[str, list]] = {
        dt: {s: [] for s in STRATEGIES} for dt in DAY_TYPES
    }
    day_type_count: Dict[str, int] = defaultdict(int)
    daily_details = []

    for d, day_candles in sorted(days.items()):
        day_type, metrics = classify_day(day_candles, avg_orb_range)
        day_type_count[day_type] += 1

        if day_type in ("UNDEFINED", "AWAITING"):
            continue

        day_row = {"date": str(d), "day_type": day_type}

        for strat_name, strat_fn in STRATEGIES.items():
            result = strat_fn(day_candles, metrics)
            if result is None:
                pnl_pts, hit = 0.0, "NO_TRADE"
            else:
                pnl_pts, hit, _ = result
            custo = _custo(PARAMS[strat_name]["stop"]) if hit != "NO_TRADE" else 0.0
            pnl_brl = round(pnl_pts * WIN_PONTO - custo, 2) if hit != "NO_TRADE" else 0.0
            results[day_type][strat_name].append(pnl_brl)
            day_row[strat_name] = f"{pnl_brl:+.1f} ({hit})"

        daily_details.append(day_row)

    return {
        "results":       results,
        "day_type_count": dict(day_type_count),
        "avg_orb_range": avg_orb_range,
        "n_days":        len(days),
        "daily_details": daily_details,
    }


# --- Análise e Relatório ------------------------------------------------------

def _analyze(trades: list) -> dict:
    """Calcula métricas de uma lista de P&Ls."""
    if not trades or len(trades) == 0:
        return {"n": 0, "wr": 0.0, "avg": 0.0, "pf": 0.0, "total": 0.0}
    wins   = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    total_win  = sum(wins)   if wins   else 0
    total_loss = abs(sum(losses)) if losses else 0
    wr = len(wins) / len(trades)
    pf = total_win / total_loss if total_loss > 0 else (99.0 if total_win > 0 else 0.0)
    return {
        "n":     len(trades),
        "wr":    wr,
        "avg":   sum(trades) / len(trades),
        "pf":    pf,
        "total": sum(trades),
    }


def print_report(data: dict):
    """Imprime o relatório completo no terminal."""
    results       = data["results"]
    day_counts    = data["day_type_count"]
    avg_range     = data["avg_orb_range"]
    n_days        = data["n_days"]

    print("\n" + "="*80)
    print(" BACKTEST POR TIPO DE DIA - WIN MINI FUTURO")
    print(f" Pregoes analisados: {n_days} | Range medio ORB: {avg_range:.0f} pts")
    print("="*80)

    # Distribuição dos tipos de dia
    print("\n[DISTRIBUICAO DOS TIPOS DE DIA]")
    for dt in DAY_TYPES:
        cnt = day_counts.get(dt, 0)
        pct = cnt / n_days * 100 if n_days > 0 else 0
        bar = "#" * int(pct / 3)
        print(f"  {dt:<15} {cnt:>3} dias ({pct:4.1f}%) {bar}")

    # Tabela cruzada: estratégia × tipo de dia
    print("\n[PERFORMANCE POR TIPO DE DIA x ESTRATEGIA]")
    print(f"  {'Estrategia':<15}", end="")
    for dt in ["TREND_UP", "TREND_DOWN", "RANGE_NORMAL", "RANGE_WIDE"]:
        print(f"  {dt:<15}", end="")
    print()
    print("  " + "-"*75)

    best_per_day_type: Dict[str, Tuple[str, float]] = {}  # {day_type: (strat, pf)}

    for strat in STRATEGIES:
        print(f"  {strat:<15}", end="")
        for dt in ["TREND_UP", "TREND_DOWN", "RANGE_NORMAL", "RANGE_WIDE"]:
            trades = results[dt][strat]
            m = _analyze(trades)
            if m["n"] == 0:
                print(f"  {'---':^15}", end="")
                continue
            cell = f"WR={m['wr']*100:.0f}% PF={m['pf']:.2f}"
            print(f"  {cell:<15}", end="")
            # Atualiza melhor estrategia por tipo
            if dt not in best_per_day_type or m["pf"] > best_per_day_type[dt][1]:
                best_per_day_type[dt] = (strat, m["pf"])
        print()

    # Melhores estratégias por tipo de dia
    print("\n MELHOR ESTRATEGIA POR TIPO DE DIA:")
    print("  " + "-"*55)
    recommendations = {}
    for dt in ["TREND_UP", "TREND_DOWN", "RANGE_NORMAL", "RANGE_WIDE"]:
        # Ranking completo por PF
        ranking = []
        for strat in STRATEGIES:
            trades = results[dt][strat]
            m = _analyze(trades)
            if m["n"] >= 5:  # mínimo 5 trades para ser significativo
                ranking.append((strat, m))
        ranking.sort(key=lambda x: x[1]["pf"], reverse=True)

        if ranking:
            best_strat, best_m = ranking[0]
            recommendations[dt] = {
                "primary":    best_strat,
                "pf":         best_m["pf"],
                "wr":         best_m["wr"],
                "avg_pnl":    best_m["avg"],
                "total_pnl":  best_m["total"],
                "n_trades":   best_m["n"],
                "ranking":    [(s, m["pf"]) for s, m in ranking],
            }
            print(f"  {dt:<15}  1st {best_strat:<15} WR={best_m['wr']*100:.0f}%  "
                  f"PF={best_m['pf']:.2f}  Avg={best_m['avg']:+.1f}R$  "
                  f"Total={best_m['total']:+.1f}R$  (n={best_m['n']})")
            for i, (rs, rm) in enumerate(ranking[1:3], 2):
                print(f"  {'':15}  {i}nd {rs:<15} PF={rm['pf']:.2f}  WR={rm['wr']*100:.0f}%  Total={rm['total']:+.1f}R$")
        else:
            print(f"  {dt:<15}  dados insuficientes (<5 trades)")

    # Mostra detalhes por estratégia total
    print("\n TOTAL ACUMULADO POR ESTRATÉGIA (todos os tipos de dia):")
    print("  " + "-"*55)
    all_strat_totals = {}
    for strat in STRATEGIES:
        all_trades = []
        for dt in DAY_TYPES:
            all_trades += results[dt][strat]
        m = _analyze(all_trades)
        all_strat_totals[strat] = m
        print(f"  {strat:<15}  n={m['n']:>3}  WR={m['wr']*100:.0f}%  "
              f"PF={m['pf']:.2f}  Total={m['total']:+.1f}R$")

    print("\n" + "="*80)
    print(" RECOMENDACAO: usar estas estratégias no day_classifier.py")
    print("="*80)
    for dt, rec in recommendations.items():
        print(f"  {dt:<15} -> {rec['primary']}")
    print()

    return recommendations


# --- Main ---------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  BACKTEST POR TIPO DE DIA - WIN MINI FUTURO")
    print("=" * 60)

    # Conecta ao MT5
    mt5 = _connect_mt5()
    if mt5 is None:
        print("\n[ERRO] MT5 não disponível. Execute este script no Windows com MT5 aberto.")
        sys.exit(1)

    # Tenta primeiro WINM26 (contrato mais recente), depois WINJ26
    candles = []
    for sym in ["WINM26", "WINJ26", "WIN$"]:
        print(f"\n[backtest] Tentando símbolo: {sym}")
        candles = _fetch_candles(mt5, sym, LOOKBACK)
        if candles:
            print(f"[backtest] {len(candles)} candles M5 obtidos de {sym}")
            break

    if not candles:
        print("[ERRO] Nenhum candle obtido. Verifique símbolo e conexão MT5.")
        mt5.shutdown()
        sys.exit(1)

    # Executa backtest
    data = run_backtest(candles)

    # Relatório
    recommendations = print_report(data)

    # Salva recomendações em JSON para o day_classifier usar
    import json
    out_path = os.path.join(_ROOT, "data", "day_type_recommendations.json")
    with open(out_path, "w", encoding="utf-8") as f:
        # Serializa sem as listas grandes de trades individuais
        save_data = {
            "generated_at":    datetime.now(_BRT).isoformat(),
            "n_days":          data["n_days"],
            "avg_orb_range":   data["avg_orb_range"],
            "day_type_count":  data["day_type_count"],
            "recommendations": recommendations,
        }
        json.dump(save_data, f, indent=2, ensure_ascii=False)
    print(f"\n Recomendacoes salvas em: {out_path}")

    # Detalhe diário (últimos 10 dias)
    print("\n ULTIMOS 10 PREGOES ANALISADOS:")
    for row in data["daily_details"][-10:]:
        print(f"  {row['date']}  {row['day_type']:<15}", end="")
        for s in list(STRATEGIES.keys())[:3]:
            v = row.get(s, "-")
            print(f"  {s}:{v}", end="")
        print()

    mt5.shutdown()
    print("\n[backtest] Concluido.")
