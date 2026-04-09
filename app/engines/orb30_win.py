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

# Day Classifier — classifica regime do dia automaticamente
try:
    from app.engines import day_classifier as _day_clf
except ImportError:
    try:
        import day_classifier as _day_clf
    except ImportError:
        _day_clf = None

_BRT = timezone(timedelta(hours=-3))

# ── Parâmetros configuráveis ──────────────────────────────────────────────────
WIN_SYMBOL     = os.getenv("WIN_SYMBOL", "WINJ26")
WIN_STOP       = int(os.getenv("WIN_STOP",   "150"))
WIN_TARGET     = int(os.getenv("WIN_TARGET", "300"))
WIN_CAPITAL    = float(os.getenv("WIN_CAPITAL", "612.0"))
WIN_PONTO      = 0.20   # R$ por ponto por contrato
WIN_LIVE_ORDER = os.getenv("WIN_LIVE_ORDER", "false").strip().lower() == "true"

_CUSTO_PCT  = 0.04   # 4% do risco (spread + corretagem)
_ORB_CANDLES = 6     # Primeiros 6 candles M5 = 30 minutos de range

# ── Filtro de tendência (evita entrar contra trend days) ─────────────────────
_TREND_BLOCK_PTS = float(os.getenv("WIN_TREND_BLOCK_PTS", "500.0"))  # pts WIN
_TREND_CANDLES   = int(os.getenv("WIN_TREND_CANDLES",   "6"))         # janela

# ── Filtro KST Diário (Martin Pring — melhora WR 36%→46%, PF 1.11→1.62) ─────
WIN_KST_FILTER   = os.getenv("WIN_KST_FILTER", "true").strip().lower() == "true"
_KST_ROCS        = [10, 15, 20, 30]    # períodos ROC
_KST_SMAS        = [10, 10, 10, 15]    # suavização de cada ROC
_KST_WEIGHTS     = [1,  2,  3,  4]     # pesos ponderados
_KST_SIGNAL_PER  = 9                   # período da linha de sinal


# ── Estado diário (persiste em memória entre ciclos do mesmo dia) ─────────────
_state: Dict[str, Any] = {
    "trading_date":  None,   # date do dia atual de trading
    "orb_high":      None,   # máxima dos primeiros 30min
    "orb_low":       None,   # mínima dos primeiros 30min
    "orb_locked":    False,  # True após os 6 candles se formarem
    "orb_override":  False,  # True = ORB fixado manualmente (não será resetado)
    "position":      None,   # None | "LONG" | "SHORT"
    "entry_price":   None,   # preço de entrada (pontos)
    "entry_time":    None,   # datetime de entrada
    "ticket":        None,   # ticket MT5 da ordem aberta (int)
    "traded_today":  False,  # já operou hoje
    "last_pnl":      0.0,    # P&L da última operação fechada
    "last_result":   None,    # "ALVO" | "STOP" | "CLOSE_EOD" | None
    "cum_pnl":       0.0,    # P&L acumulado desde o início
    "cycle_pnl":     0.0,    # P&L do ciclo atual (reset a cada chamada)
    "kst_daily_bias": 0,     # 1=KST bullish, -1=KST bearish, 0=indefinido
    "day_type":       None,   # "TREND_UP" | "TREND_DOWN" | "RANGE" | "UNDEFINED" | None
    "day_strength":   0.0,    # força do regime (0.0-1.0)
    "day_override":   False,  # True = classificador fez override do KST
    "day_reason":     "",     # descrição da classificação (para logs/dashboard)
    "recommended_strategy": None,  # estratégia recomendada pelo backtest para o tipo de dia
}


def _now_brt() -> datetime:
    return datetime.now(_BRT)


def _reset_day():
    """Reseta o estado no início de cada sessão de trading."""
    if not _state.get("orb_override"):
        _state["orb_high"]     = None
        _state["orb_low"]      = None
        _state["orb_locked"]   = False
    else:
        _state["orb_override"] = False  # override vale só 1 dia
    _state["position"]     = None
    _state["entry_price"]  = None
    _state["entry_time"]   = None
    _state["ticket"]       = None
    _state["traded_today"] = False
    _state["last_pnl"]     = 0.0
    _state["last_result"]  = None
    _state["cycle_pnl"]    = 0.0
    _state["day_type"]     = None
    _state["day_strength"] = 0.0
    _state["day_override"] = False
    _state["day_reason"]   = ""
    _state["recommended_strategy"] = None
    # Recalcula viés KST D1 no início de cada sessão
    _update_kst_daily_bias()


# ── Setters de restauração (persistência entre restarts) ─────────────────────
def set_traded_today(value: bool):
    """BUG5 fix: restaura traded_today após restart."""
    _state["traded_today"] = bool(value)

def set_cum_pnl(value: float):
    """BUG5 fix: restaura cum_pnl após restart."""
    _state["cum_pnl"] = float(value)

def set_trading_date(value):
    """BUG5 fix: restaura trading_date — evita _reset_day() espúrio no restart."""
    if value is None:
        return
    if isinstance(value, str) and value:
        try:
            _state["trading_date"] = date.fromisoformat(value)
        except ValueError:
            pass
    elif isinstance(value, date):
        _state["trading_date"] = value

def set_open_position(direction, entry_price, ticket, stop_price=None, tp_price=None):
    """BUG5 fix: restaura posição aberta após restart."""
    if direction and ticket:
        _state["position"]    = direction
        _state["entry_price"] = float(entry_price) if entry_price else None
        _state["ticket"]      = int(ticket)
        print(f"[orb30_win] posição restaurada: {direction} ticket={ticket}", flush=True)


def _custo_brl() -> float:
    """Custo fixo por operação: 4% do risco."""
    return round(WIN_STOP * WIN_PONTO * _CUSTO_PCT, 2)


def _calc_trend_bias(candles: list, n: int) -> float:
    """Variação de preço nos últimos n candles (positivo=alta, negativo=queda)."""
    if len(candles) < n + 1:
        return 0.0
    return candles[-1]["close"] - candles[-(n + 1)]["close"]


# ── KST Diário (Pring Standard) ───────────────────────────────────────────────

def _sma_series(values: list, period: int) -> list:
    """SMA simples — retorna None onde janela incompleta ou contém None."""
    result = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        if any(v is None for v in window):
            continue
        result[i] = sum(window) / period
    return result


def _roc_series(values: list, period: int) -> list:
    """Rate of Change (%) — retorna None onde não calculável."""
    result = [None] * len(values)
    for i in range(period, len(values)):
        base = values[i - period]
        if base and base != 0.0:
            result[i] = (values[i] - base) / abs(base) * 100.0
    return result


def _calc_kst_series(closes: list):
    """
    Calcula KST (Martin Pring). Retorna (kst_line, signal_line) como listas
    indexadas igual a 'closes'. Posições insuficientes retornam None.
    Parâmetros: ROC=[10,15,20,30], SMA=[10,10,10,15], pesos=[1,2,3,4], sinal=9.
    """
    all_sma = []
    for roc_per, sma_per in zip(_KST_ROCS, _KST_SMAS):
        roc = _roc_series(closes, roc_per)
        sma = _sma_series(roc, sma_per)
        all_sma.append(sma)

    n = len(closes)
    kst_line: list = []
    for i in range(n):
        vs = [all_sma[j][i] for j in range(4)]
        if any(v is None for v in vs):
            kst_line.append(None)
        else:
            kst_line.append(sum(_KST_WEIGHTS[j] * vs[j] for j in range(4)))

    sig_line = _sma_series(kst_line, _KST_SIGNAL_PER)
    return kst_line, sig_line


def _update_kst_daily_bias():
    """
    Busca D1 no MT5, calcula KST e armazena kst_daily_bias em _state.
    Chamada uma vez por dia (_reset_day). Sem lookahead: usa pos=1 (barra fechada).
    """
    _state["kst_daily_bias"] = 0
    if not WIN_KST_FILTER or not _ensure_mt5():
        return
    try:
        if not _mt5.symbol_select(WIN_SYMBOL, True):
            return
        # pos=1 → pula a barra D1 em formação, pega 60 barras já fechadas
        rates = _mt5.copy_rates_from_pos(WIN_SYMBOL, _mt5.TIMEFRAME_D1, 1, 60)
        if rates is None or len(rates) < 55:
            print("[orb30_win] KST D1: dados insuficientes para calcular", flush=True)
            return
        closes = [float(r["close"]) for r in rates]
        kst_line, sig_line = _calc_kst_series(closes)
        # Último par (kst, signal) válido
        kst_now = sig_now = None
        for i in range(len(kst_line) - 1, -1, -1):
            if kst_line[i] is not None and sig_line[i] is not None:
                kst_now = kst_line[i]
                sig_now = sig_line[i]
                break
        if kst_now is None:
            print("[orb30_win] KST D1: não foi possível obter valor final", flush=True)
            return
        # Zero-line: KST > 0 = território positivo (bullish), KST < 0 = território negativo (bearish)
        # Mais responsivo que cruzamento com sinal (que atrasa ~9 períodos extras)
        _state["kst_daily_bias"] = 1 if kst_now > 0 else -1
        label = "ALTA ↑" if _state["kst_daily_bias"] > 0 else "BAIXA ↓"
        print(
            f"[orb30_win] KST D1: {kst_now:.4f} (zero-line) → {label}",
            flush=True,
        )
    except Exception as e:
        print(f"[orb30_win] Erro ao calcular KST D1: {e}", flush=True)
        _state["kst_daily_bias"] = 0


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

_MT5_LOGIN    = os.getenv("MT5_LOGIN", "").strip()
_MT5_PASSWORD = os.getenv("MT5_PASSWORD", "").strip()
_MT5_SERVER   = os.getenv("MT5_SERVER", "").strip()
_mt5_initialized = False


def _ensure_mt5() -> bool:
    """Garante conexão MT5 com reconexão automática via credenciais."""
    global _mt5_initialized
    if not _MT5_AVAILABLE or _mt5 is None:
        return False
    if _mt5_initialized:
        info = _mt5.account_info()
        if info is not None:
            return True
        _mt5_initialized = False
        _mt5.shutdown()
    try:
        if _MT5_LOGIN and _MT5_PASSWORD and _MT5_SERVER:
            ok = _mt5.initialize(
                login=int(_MT5_LOGIN),
                password=_MT5_PASSWORD,
                server=_MT5_SERVER,
            )
        else:
            ok = _mt5.initialize()
        if ok:
            _mt5_initialized = True
            print(f"[orb30_win] MT5 conectado OK (login={_MT5_LOGIN or '?'})", flush=True)
            return True
        print(f"[orb30_win] MT5 initialize() falhou: {_mt5.last_error()}", flush=True)
    except Exception as e:
        print(f"[orb30_win] MT5 init erro: {e}", flush=True)
    return False


def _get_candles_today(limit: int = 80) -> list:
    """Retorna candles M5 do dia de hoje para WIN_SYMBOL."""
    if not _ensure_mt5():
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
            # MT5 XP retorna timestamp como hora local do servidor (BRT)
            # NÃO é epoch UTC padrão — tratar como BRT direto
            candle_time = datetime.utcfromtimestamp(r["time"]).replace(tzinfo=_BRT)
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
    if not _ensure_mt5():
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

def run_cycle(skip_entry: bool = False) -> Dict[str, Any]:
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
        # Posição aberta fora do horário de mercado = órfã — fechar imediatamente
        if _state["position"] is not None:
            return _manage_position(now, now)  # passa eod=now para forçar fechamento
        return _build_result("FORA_HORARIO")

    # ── 3. Busca candles M5 do dia ────────────────────────────────────────
    candles = _get_candles_today(limit=80)
    if not candles:
        # MT5 indisponível ou sem dados
        return _build_result("NO_DATA")

    # ── 4. Monta o Opening Range (primeiros 6 candles = 09:00-09:29) ──────
    _orb_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    _orb_end   = now.replace(hour=9, minute=30, second=0, microsecond=0)
    orb_candles = [c for c in candles if _orb_start <= c["time"] < _orb_end]
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
    # ── skip_entry: proteção global (hard stop, capital abaixo do mínimo, etc.) ──
    if skip_entry:
        return _build_result("SKIP_ENTRY")
    # ── 7b. Janela de ENTRADA: 09:30-12:00 BRT ───────────────────────────
    # Backtest 60d: estender de 11:00→12:00 melhora WR 38.7%→42.9% e PF 1.00→1.19
    entry_open  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    entry_close = now.replace(hour=12, minute=0,  second=0, microsecond=0)
    if now < entry_open or now >= entry_close:
        return _build_result("FORA_JANELA_ENTRADA")

    # ── 8. Busca entrada: rompimento do ORB ──────────────────────────────
    price = _current_price()
    if price is None:
        return _build_result("NO_DATA")

    orb_high = _state["orb_high"]
    orb_low  = _state["orb_low"]

    # Filtro de tendência: se mercado está em trend day forte, não entrar contra ele
    trend_bias = _calc_trend_bias(candles, _TREND_CANDLES)
    if price > orb_high and trend_bias < -_TREND_BLOCK_PTS:
        print(
            f"[orb30_win] TREND_BLOCK: variação últimos {_TREND_CANDLES} candles="
            f"{trend_bias:+.0f}pts → bloqueando LONG (threshold={-_TREND_BLOCK_PTS:.0f})",
            flush=True,
        )
        return _build_result("TREND_BLOCK")
    if price < orb_low and trend_bias > _TREND_BLOCK_PTS:
        print(
            f"[orb30_win] TREND_BLOCK: variação últimos {_TREND_CANDLES} candles="
            f"{trend_bias:+.0f}pts → bloqueando SHORT (threshold=+{_TREND_BLOCK_PTS:.0f})",
            flush=True,
        )
        return _build_result("TREND_BLOCK")

    if price > orb_high:
        # Rompimento de alta → LONG
        # Usa Day Classifier para decidir: KST não é mais um bloqueio rígido
        if _day_clf is not None:
            clf = _day_clf.classify(
                candles, orb_high, orb_low, _state["kst_daily_bias"]
            )
            # Persiste classificação no estado (visível no dashboard)
            _state["day_type"]     = clf.day_type
            _state["day_strength"] = clf.strength
            _state["day_override"] = clf.strong_override
            _state["day_reason"]   = clf.reason

            # Verifica se backtest recomenda desativar ORB30 no tipo de dia atual
            rec = _day_clf.recommended_strategy(clf)
            _state["recommended_strategy"] = rec["primary"]
            if rec.get("skip_orb30"):
                print(
                    f"[orb30_win] DAY_SKIP: {clf.day_type} → {rec['reason']}",
                    flush=True,
                )
                return _build_result("DAY_SKIP")

            print(f"[orb30_win] DayClassifier: {clf.day_type} | {clf.reason}", flush=True)
            can_enter, entry_reason = _day_clf.should_enter_long(
                clf, _state["kst_daily_bias"], WIN_KST_FILTER
            )
            if not can_enter:
                print(f"[orb30_win] ENTRY_BLOCK (LONG): {entry_reason}", flush=True)
                return _build_result("KST_BLOCK")
            if clf.strong_override:
                print(f"[orb30_win] OVERRIDE: {entry_reason}", flush=True)
        else:
            # Fallback: lógica original se day_classifier não disponível
            if WIN_KST_FILTER and _state["kst_daily_bias"] == -1:
                print("[orb30_win] KST_BLOCK: KST D1 BEARISH → bloqueando LONG", flush=True)
                return _build_result("KST_BLOCK")
        _enter_position("LONG", price, now)
        return _build_result("LONG")

    if price < orb_low:
        # Rompimento de baixa → SHORT
        # Usa Day Classifier para decidir: KST não é mais um bloqueio rígido
        if _day_clf is not None:
            clf = _day_clf.classify(
                candles, orb_high, orb_low, _state["kst_daily_bias"]
            )
            _state["day_type"]     = clf.day_type
            _state["day_strength"] = clf.strength
            _state["day_override"] = clf.strong_override
            _state["day_reason"]   = clf.reason

            # Verifica se backtest recomenda desativar ORB30 no tipo de dia atual
            rec = _day_clf.recommended_strategy(clf)
            _state["recommended_strategy"] = rec["primary"]
            if rec.get("skip_orb30"):
                print(
                    f"[orb30_win] DAY_SKIP: {clf.day_type} → {rec['reason']}",
                    flush=True,
                )
                return _build_result("DAY_SKIP")

            print(f"[orb30_win] DayClassifier: {clf.day_type} | {clf.reason}", flush=True)
            can_enter, entry_reason = _day_clf.should_enter_short(
                clf, _state["kst_daily_bias"], WIN_KST_FILTER
            )
            if not can_enter:
                print(f"[orb30_win] ENTRY_BLOCK (SHORT): {entry_reason}", flush=True)
                return _build_result("KST_BLOCK")
            if clf.strong_override:
                print(f"[orb30_win] OVERRIDE: {entry_reason}", flush=True)
        else:
            # Fallback: lógica original se day_classifier não disponível
            if WIN_KST_FILTER and _state["kst_daily_bias"] == 1:
                print("[orb30_win] KST_BLOCK: KST D1 BULLISH → bloqueando SHORT", flush=True)
                return _build_result("KST_BLOCK")
        _enter_position("SHORT", price, now)
        return _build_result("SHORT")

    return _build_result("AGUARDANDO_BREAK")


def _enter_position(direction: str, price: float, now: datetime):
    """Entra na posicao ORB — envia ordem real ao MT5 se disponivel."""
    exec_price = price
    ticket = None

    if WIN_LIVE_ORDER and _ensure_mt5():
        try:
            tick = _mt5.symbol_info_tick(WIN_SYMBOL)
            if tick:
                exec_price = tick.ask if direction == "LONG" else tick.bid
                sl_price   = round(exec_price - WIN_STOP   if direction == "LONG" else exec_price + WIN_STOP,   0)
                tp_price   = round(exec_price + WIN_TARGET if direction == "LONG" else exec_price - WIN_TARGET, 0)
                order_type = _mt5.ORDER_TYPE_BUY if direction == "LONG" else _mt5.ORDER_TYPE_SELL
                request = {
                    "action":       _mt5.TRADE_ACTION_DEAL,
                    "symbol":       WIN_SYMBOL,
                    "volume":       1.0,
                    "type":         order_type,
                    "price":        exec_price,
                    "sl":           sl_price,
                    "tp":           tp_price,
                    "deviation":    30,
                    "magic":        20260315,
                    "comment":      "orb30_bot",
                    "type_time":    _mt5.ORDER_TIME_DAY,
                    "type_filling": _mt5.ORDER_FILLING_IOC,
                }
                result = _mt5.order_send(request)
                if result and result.retcode == _mt5.TRADE_RETCODE_DONE:
                    ticket = result.order
                    if result.price and result.price > 0:
                        exec_price = result.price  # fill price se disponível
                    # else mantém exec_price do tick (mais preciso que result.price=0)
                    print(
                        f"[orb30_win] ORDEM REAL {direction} {WIN_SYMBOL} @ {exec_price:.0f} "
                        f"| SL={sl_price:.0f} TP={tp_price:.0f} | Ticket={ticket}",
                        flush=True,
                    )
                else:
                    code = result.retcode if result else "N/A"
                    print(f"[orb30_win] FALHA ordem MT5: retcode={code} — operacao simulada", flush=True)
        except Exception as e:
            print(f"[orb30_win] Erro ao enviar ordem MT5: {e} — operacao simulada", flush=True)

    _state["position"]     = direction
    _state["entry_price"]  = exec_price
    _state["entry_time"]   = now.isoformat()
    _state["traded_today"] = True
    _state["ticket"]       = ticket
    side = "COMPRA" if direction == "LONG" else "VENDA"
    print(
        f"[orb30_win] {side} {WIN_SYMBOL} @ {exec_price:.0f} | "
        f"Stop: {exec_price - WIN_STOP if direction == 'LONG' else exec_price + WIN_STOP:.0f} "
        f"| Alvo: {exec_price + WIN_TARGET if direction == 'LONG' else exec_price - WIN_TARGET:.0f}"
        f"{' [REAL]' if ticket else ' [SIM]'}",
        flush=True,
    )


def _close_mt5_position(ticket: int, reason: str, direction: str, entry: float) -> float:
    """Fecha posicao MT5 pelo ticket. Retorna PnL real em BRL (0 se falhou)."""
    if not _MT5_AVAILABLE or _mt5 is None or not ticket:
        return 0.0
    try:
        pos = _mt5.positions_get(ticket=ticket)
        if not pos or len(pos) == 0:
            return 0.0
        p = pos[0]
        sym_mt5 = p.symbol
        tick = _mt5.symbol_info_tick(sym_mt5)
        close_price = tick.bid if p.type == 0 else tick.ask  # buy→sell at bid; sell→buy at ask
        close_type  = _mt5.ORDER_TYPE_SELL if p.type == 0 else _mt5.ORDER_TYPE_BUY
        request = {
            "action":       _mt5.TRADE_ACTION_DEAL,
            "symbol":       sym_mt5,
            "volume":       p.volume,
            "type":         close_type,
            "position":     ticket,
            "price":        close_price,
            "deviation":    30,
            "magic":        20260315,
            "comment":      f"close_{reason}",
            "type_time":    _mt5.ORDER_TIME_GTC,
            "type_filling": _mt5.ORDER_FILLING_IOC,
        }
        result = _mt5.order_send(request)
        if result and result.retcode == _mt5.TRADE_RETCODE_DONE:
            print(f"[orb30_win] FECHOU {reason} ticket={ticket} @ {result.price:.0f}", flush=True)
            return round(p.profit, 2)
        else:
            code = result.retcode if result else "N/A"
            print(f"[orb30_win] Falha ao fechar ticket={ticket}: retcode={code}", flush=True)
    except Exception as e:
        print(f"[orb30_win] Erro ao fechar posicao {ticket}: {e}", flush=True)
    return 0.0


def _manage_position(now: datetime, eod_close: datetime) -> Dict[str, Any]:
    """Verifica stop/alvo/EOD para posicao aberta — usa dados reais MT5 se disponivel."""
    direction = _state["position"]
    entry     = _state["entry_price"]
    custo     = _custo_brl()
    ticket    = _state.get("ticket")

    hit      = None
    pnl_brl  = 0.0

    # ── Via MT5 real (ticket disponivel) ─────────────────────────────────
    if _MT5_AVAILABLE and _mt5 is not None and ticket:
        try:
            pos = _mt5.positions_get(ticket=ticket)
            if not pos or len(pos) == 0:
                # Posicao ja foi fechada automaticamente (SL/TP atingido)
                # Busca o PnL real nos deals historicos
                deals = _mt5.history_deals_get(position=ticket)
                if deals and len(deals) > 0:
                    pnl_brl = round(sum(float(d.profit) for d in deals), 2)
                    # Determina se foi stop ou alvo pelo PnL
                    if pnl_brl >= 0:
                        hit = "ALVO"
                    else:
                        hit = "STOP"
                else:
                    hit = "CLOSE_UNKNOWN"
                    pnl_brl = 0.0
            elif now >= eod_close:
                # Posicao aberta no fim do dia — fecha compulsoriamente
                pnl_brl = _close_mt5_position(ticket, "EOD", direction, entry)
                hit = "CLOSE_EOD"
        except Exception as e:
            print(f"[orb30_win] Erro ao verificar posicao MT5: {e}", flush=True)
            # Fallback para logica simulada

    # ── Fallback simulado (sem MT5 real) ─────────────────────────────────
    if hit is None:
        candles = _get_candles_today(limit=10)
        price   = _current_price()

        if candles:
            last = candles[-1]
            if direction == "LONG":
                stop_p = entry - WIN_STOP
                alvo_p = entry + WIN_TARGET
                if last["low"] <= stop_p:
                    hit = "STOP"
                    price_hit = stop_p
                elif last["high"] >= alvo_p:
                    hit = "ALVO"
                    price_hit = alvo_p
                else:
                    price_hit = last["close"]
            else:  # SHORT
                stop_p = entry + WIN_STOP
                alvo_p = entry - WIN_TARGET
                if last["high"] >= stop_p:
                    hit = "STOP"
                    price_hit = stop_p
                elif last["low"] <= alvo_p:
                    hit = "ALVO"
                    price_hit = alvo_p
                else:
                    price_hit = last["close"]
        else:
            price_hit = price or entry

        # Fechamento compulsorio no fim do dia
        if hit is None and now >= eod_close and price is not None:
            price_hit = price
            hit = "CLOSE_EOD"

        if hit is not None:
            result_pts = 0.0
            if hit == "ALVO":
                result_pts = WIN_TARGET
            elif hit == "STOP":
                result_pts = -WIN_STOP
            else:
                result_pts = (price_hit - entry) * (1 if direction == "LONG" else -1)
            pnl_brl = round(result_pts * WIN_PONTO - custo, 2)

    # ── Aplica fechamento ────────────────────────────────────────────────
    if hit is not None:
        _state["last_pnl"]    = pnl_brl
        _state["last_result"] = hit
        _state["cycle_pnl"]   = pnl_brl
        _state["cum_pnl"]     = round(_state["cum_pnl"] + pnl_brl, 2)
        _state["position"]    = None
        _state["entry_price"] = None
        _state["ticket"]      = None
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
        "engine":        "ORB30_WIN",
        "symbol":        WIN_SYMBOL,
        "signal":        signal,
        "position":      _state["position"],
        "entry_price":   _state["entry_price"],
        "orb_high":      _state["orb_high"],
        "orb_low":       _state["orb_low"],
        "cycle_pnl":     _state["cycle_pnl"],
        "cum_pnl":       _state["cum_pnl"],
        "last_result":   _state["last_result"],
        "traded_today":  _state["traded_today"],
        "ticket":        _state.get("ticket"),
        "live_order":    _state.get("ticket") is not None,
        "live_order_enabled": WIN_LIVE_ORDER,
        "capital":       WIN_CAPITAL,
        "stop_pts":      WIN_STOP,
        "target_pts":    WIN_TARGET,
        "mt5_available": _MT5_AVAILABLE,
        "kst_bias":      _state.get("kst_daily_bias", 0),
        "day_type":      _state.get("day_type"),
        "day_strength":  _state.get("day_strength", 0.0),
        "day_override":  _state.get("day_override", False),
        "day_reason":    _state.get("day_reason", ""),
        "recommended_strategy": _state.get("recommended_strategy"),
    }


def status() -> Dict[str, Any]:
    """Retorna estado atual do engine (para endpoint /status ou dashboard)."""
    return _build_result(_state.get("position") or "IDLE")
