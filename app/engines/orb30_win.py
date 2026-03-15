"""
ORB-30 WIN Engine — Opening Range Breakout 30 minutos para WIN Mini-Futuro

Estratégia validada pelo backtest Dez/25-Mar/26:
  WR=51.7%  |  Fator=2.02  |  Retorno=+89%  |  DD max=25.7%  com R$500

Regras:
  1. Mercado abre às 09:00 BRT
  2. Os primeiros 6 candles M5 (09:00-09:29) formam o Opening Range
  3. A partir de 09:30, aguarda primeiro rompimento acima do high ou abaixo do low
  4. Stop: 150 pontos | Alvo: 300 pontos | 1 trade por dia
  5. Fecha posição remanescente às 17:00 BRT (evita virar overnight)
  6. Custo embutido: 4% do risco (spread + corretagem XP)

Parâmetros (configuráveis via ENV):
  WIN_SYMBOL   = WINM26   (contrato ativo — atualizar no vencimento)
  WIN_STOP     = 150      (pontos)
  WIN_TARGET   = 300      (pontos)
  WIN_CAPITAL  = 500.0    (R$ alocado para essa estratégia)
"""

import os
import sys
from datetime import datetime, date, timezone, timedelta
from typing import Optional, Dict, Any

_BRT = timezone(timedelta(hours=-3))

# ── Parâmetros configuráveis ──────────────────────────────────────────────────
WIN_SYMBOL  = os.getenv("WIN_SYMBOL", "WINM26")
WIN_STOP    = int(os.getenv("WIN_STOP",   "150"))
WIN_TARGET  = int(os.getenv("WIN_TARGET", "300"))
WIN_CAPITAL = float(os.getenv("WIN_CAPITAL", "500.0"))
WIN_PONTO   = 0.20   # R$ por ponto por contrato

_CUSTO_PCT  = 0.04   # 4% do risco (spread + corretagem)
_ORB_CANDLES = 6     # Primeiros 6 candles M5 = 30 minutos de range


# ── Estado diário (persiste em memória entre ciclos do mesmo dia) ─────────────
_state: Dict[str, Any] = {
    "trading_date":  None,   # date do dia atual de trading
    "orb_high":      None,   # máxima dos primeiros 30min
    "orb_low":       None,   # mínima dos primeiros 30min
    "orb_locked":    False,  # True após os 6 candles se formarem
    "position":      None,   # None | "LONG" | "SHORT"
    "entry_price":   None,   # preço de entrada (pontos)
    "entry_time":    None,   # datetime de entrada
    "traded_today":  False,  # já operou hoje
    "last_pnl":      0.0,    # P&L da última operação fechada
    "last_result":   None,    # "ALVO" | "STOP" | "CLOSE_EOD" | None
    "cum_pnl":       0.0,    # P&L acumulado desde o início
    "cycle_pnl":     0.0,    # P&L do ciclo atual (reset a cada chamada)
}


def _now_brt() -> datetime:
    return datetime.now(_BRT)


def _reset_day():
    """Reseta o estado no início de cada sessão de trading."""
    _state["orb_high"]     = None
    _state["orb_low"]      = None
    _state["orb_locked"]   = False
    _state["position"]     = None
    _state["entry_price"]  = None
    _state["entry_time"]   = None
    _state["traded_today"] = False
    _state["last_pnl"]     = 0.0
    _state["last_result"]  = None
    _state["cycle_pnl"]    = 0.0


def _custo_brl() -> float:
    """Custo fixo por operação: 4% do risco."""
    return round(WIN_STOP * WIN_PONTO * _CUSTO_PCT, 2)


# ── MT5 helper ───────────────────────────────────────────────────────────────
_MT5_AVAILABLE = False
_mt5 = None
if sys.platform == "win32":
    try:
        import MetaTrader5 as _mt5_lib
        _mt5 = _mt5_lib
        _MT5_AVAILABLE = True
    except ImportError:
        pass


def _get_candles_today(limit: int = 80) -> list:
    """Retorna candles M5 do dia de hoje para WIN_SYMBOL."""
    if not _MT5_AVAILABLE or _mt5 is None:
        return []
    try:
        # Garante que o símbolo está disponível
        if not _mt5.symbol_select(WIN_SYMBOL, True):
            return []
        rates = _mt5.copy_rates_from_pos(WIN_SYMBOL, _mt5.TIMEFRAME_M5, 0, limit)
        if rates is None or len(rates) == 0:
            return []
        today = _now_brt().date()
        candles = []
        for r in rates:
            candle_time = datetime.fromtimestamp(r["time"], tz=_BRT)
            if candle_time.date() == today:
                candles.append({
                    "time":  candle_time,
                    "open":  float(r["open"]),
                    "high":  float(r["high"]),
                    "low":   float(r["low"]),
                    "close": float(r["close"]),
                    "vol":   float(r["tick_volume"]),
                })
        return candles
    except Exception as e:
        print(f"[orb30_win] Erro ao buscar candles: {e}", flush=True)
        return []


def _current_price() -> Optional[float]:
    """Retorna última cotação (bid+ask)/2 do WIN."""
    if not _MT5_AVAILABLE or _mt5 is None:
        return None
    try:
        tick = _mt5.symbol_info_tick(WIN_SYMBOL)
        if tick:
            return (tick.ask + tick.bid) / 2.0
        # Fallback: último close
        rates = _mt5.copy_rates_from_pos(WIN_SYMBOL, _mt5.TIMEFRAME_M5, 0, 1)
        if rates and len(rates) > 0:
            return float(rates[0]["close"])
    except Exception:
        pass
    return None


# ── Lógica principal ──────────────────────────────────────────────────────────

def run_cycle() -> Dict[str, Any]:
    """
    Executa um ciclo ORB-30 WIN.
    Deve ser chamado a cada ciclo do bot (a cada N minutos).

    Retorna dict com:
      cycle_pnl   — P&L gerado nesse ciclo (0 se sem trade)
      signal      — "LONG" | "SHORT" | "HOLD" | "NONE" | "NO_DATA"
      position    — posição atual
      orb_high/low, entry_price, status
    """
    _state["cycle_pnl"] = 0.0
    now = _now_brt()
    today = now.date()

    # ── 1. Reset no início de cada dia de trading ─────────────────────────
    if _state["trading_date"] != today:
        _state["trading_date"] = today
        _reset_day()
        print(f"[orb30_win] Nova sessão: {today} | Capital R${WIN_CAPITAL:.0f}", flush=True)

    # ── 2. Verifica horário de mercado WIN (09:00-17:55 BRT) ─────────────
    market_open  = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
    market_close = now.replace(hour=17, minute=55, second=0, microsecond=0)
    eod_close    = now.replace(hour=17, minute=0,  second=0, microsecond=0)  # fecha posição às 17h

    if now < market_open or now > market_close:
        return _build_result("FORA_HORARIO")

    # ── 3. Busca candles M5 do dia ────────────────────────────────────────
    candles = _get_candles_today(limit=80)
    if not candles:
        # MT5 indisponível ou sem dados
        return _build_result("NO_DATA")

    # ── 4. Monta o Opening Range (primeiros 6 candles = 09:00-09:29) ──────
    orb_candles = [c for c in candles if c["time"] < now.replace(hour=9, minute=30, second=0)]
    if len(orb_candles) >= _ORB_CANDLES and not _state["orb_locked"]:
        _state["orb_high"]   = max(c["high"]  for c in orb_candles)
        _state["orb_low"]    = min(c["low"]   for c in orb_candles)
        _state["orb_locked"] = True
        print(
            f"[orb30_win] ORB formado: High={_state['orb_high']:.0f} "
            f"Low={_state['orb_low']:.0f} ({len(orb_candles)} candles)",
            flush=True,
        )

    # ── 5. Ainda sem ORB formado — aguardando ─────────────────────────────
    if not _state["orb_locked"]:
        return _build_result("AGUARDANDO_ORB")

    # ── 6. Gerencia posição aberta ────────────────────────────────────────
    if _state["position"] is not None:
        return _manage_position(now, eod_close)

    # ── 7. Já operou hoje — não re-entra ─────────────────────────────────
    if _state["traded_today"]:
        return _build_result("JA_OPEROU")

    # ── 8. Busca entrada: rompimento do ORB ──────────────────────────────
    price = _current_price()
    if price is None:
        return _build_result("NO_DATA")

    orb_high = _state["orb_high"]
    orb_low  = _state["orb_low"]

    if price > orb_high:
        # Rompimento de alta → LONG
        _enter_position("LONG", price, now)
        return _build_result("LONG")

    if price < orb_low:
        # Rompimento de baixa → SHORT
        _enter_position("SHORT", price, now)
        return _build_result("SHORT")

    return _build_result("AGUARDANDO_BREAK")


def _enter_position(direction: str, price: float, now: datetime):
    _state["position"]    = direction
    _state["entry_price"] = price
    _state["entry_time"]  = now.isoformat()
    _state["traded_today"] = True
    side = "COMPRA" if direction == "LONG" else "VENDA"
    print(
        f"[orb30_win] {side} WINM26 @ {price:.0f} | "
        f"Stop: {price - WIN_STOP if direction == 'LONG' else price + WIN_STOP:.0f} "
        f"| Alvo: {price + WIN_TARGET if direction == 'LONG' else price - WIN_TARGET:.0f}",
        flush=True,
    )


def _manage_position(now: datetime, eod_close: datetime) -> Dict[str, Any]:
    """Verifica stop/alvo/EOD para posição aberta."""
    direction    = _state["position"]
    entry        = _state["entry_price"]
    custo        = _custo_brl()

    # Tenta buscar candles recentes para verificar stop/alvo hit
    candles = _get_candles_today(limit=10)
    price   = _current_price()

    hit = None
    result_pts = 0.0

    if candles:
        last = candles[-1]
        if direction == "LONG":
            stop_p = entry - WIN_STOP
            alvo_p = entry + WIN_TARGET
            if last["low"] <= stop_p:
                result_pts = -WIN_STOP
                hit = "STOP"
            elif last["high"] >= alvo_p:
                result_pts = WIN_TARGET
                hit = "ALVO"
        else:  # SHORT
            stop_p = entry + WIN_STOP
            alvo_p = entry - WIN_TARGET
            if last["high"] >= stop_p:
                result_pts = -WIN_STOP
                hit = "STOP"
            elif last["low"] <= alvo_p:
                result_pts = WIN_TARGET
                hit = "ALVO"

    # Fechamento compulsório no fim do dia
    if hit is None and now >= eod_close and price is not None:
        result_pts = (price - entry) * (1 if direction == "LONG" else -1)
        hit = "CLOSE_EOD"

    if hit is not None:
        pnl_brl = round(result_pts * WIN_PONTO - custo, 2)
        _state["last_pnl"]    = pnl_brl
        _state["last_result"] = hit
        _state["cycle_pnl"]   = pnl_brl
        _state["cum_pnl"]     = round(_state["cum_pnl"] + pnl_brl, 2)
        _state["position"]    = None
        _state["entry_price"] = None
        sinal = "+" if pnl_brl >= 0 else ""
        print(
            f"[orb30_win] {hit} {direction} | "
            f"P&L: R${sinal}{pnl_brl:.2f} | Acum: R${_state['cum_pnl']:.2f}",
            flush=True,
        )
        return _build_result(f"FECHOU_{hit}")

    return _build_result("HOLD")


def _build_result(signal: str) -> Dict[str, Any]:
    return {
        "engine":      "ORB30_WIN",
        "symbol":      WIN_SYMBOL,
        "signal":      signal,
        "position":    _state["position"],
        "entry_price": _state["entry_price"],
        "orb_high":    _state["orb_high"],
        "orb_low":     _state["orb_low"],
        "cycle_pnl":   _state["cycle_pnl"],
        "cum_pnl":     _state["cum_pnl"],
        "last_result": _state["last_result"],
        "traded_today": _state["traded_today"],
        "capital":     WIN_CAPITAL,
        "stop_pts":    WIN_STOP,
        "target_pts":  WIN_TARGET,
        "mt5_available": _MT5_AVAILABLE,
    }


def status() -> Dict[str, Any]:
    """Retorna estado atual do engine (para endpoint /status ou dashboard)."""
    return _build_result(_state.get("position") or "IDLE")
