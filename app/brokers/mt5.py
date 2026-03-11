"""
MetaTrader 5 Broker — B3 + Forex via plataforma MT5 (Windows).

Funcionalidades:
  - Market data real-time (cotações B3, Forex, Indices)
  - Histórico de candles OHLCV
  - Execução de ordens via corretora conectada ao MT5
  - Consulta de saldo e posições

IMPORTANTE — Requisitos:
  - Windows apenas (biblioteca MetaTrader5 só funciona em Windows)
  - MetaTrader 5 instalado e logado em uma corretora parceira
  - Corretoras BR compatíveis: Genial, Vítreo, Clear, XP (via parceria MT5),
    Rico, Órama, Modal, Mirae, CM Capital, etc.
  - Em Railway / Linux: broker desabilitado automaticamente (graceful skip)

Setup:
  1. Baixe MT5 da corretora: https://www.metatrader5.com/pt/download
  2. Logue com sua conta de investidor na corretora
  3. Configure env vars:
       MT5_LOGIN=12345678           (nº da conta na corretora)
       MT5_PASSWORD=sua_senha
       MT5_SERVER=NomeDaCorretora-Demo  (ou NomeDaCorretora-Real)
  4. Para o bot local: execute com MT5 aberto na mesma máquina
  5. Railway: MT5 ficará desabilitado (sem as env vars, fallback Yahoo/BRAPI)

Notas:
  - Valores em B3 retornados em BRL nativamente
  - Forex e índices em USD/ponto
  - Order lots em B3: 1 lote = 100 ações (mini: 1 ação = 1 lote no Clear/Genial)
"""

import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.config import settings

# MetaTrader5 só está disponível no Windows
_MT5_AVAILABLE = False
_mt5 = None
if sys.platform == "win32":
    try:
        import MetaTrader5 as mt5  # type: ignore
        _mt5 = mt5
        _MT5_AVAILABLE = True
    except ImportError:
        pass  # biblioteca não instalada — funciona sem ela

# Mapeamento: símbolo interno → símbolo MT5 (varia por corretora)
# Exemplos: na Clear = "PETR4"; na Genial = "PETR4.SA"; na XP = "PETR4"
# O bot usa o símbolo sem sufixo; se a corretora precisar de sufixo, configure MT5_SUFFIX
_B3_SUFFIX = ""  # padrão sem sufixo — ajuste via env se necessário

# Timeframe MT5 constants (carregados quando mt5 disponível)
_TF_MAP = {}


def _load_tf_map():
    global _TF_MAP
    if not _MT5_AVAILABLE or _mt5 is None:
        return
    _TF_MAP = {
        "1m":  _mt5.TIMEFRAME_M1,
        "2m":  _mt5.TIMEFRAME_M2,
        "5m":  _mt5.TIMEFRAME_M5,
        "15m": _mt5.TIMEFRAME_M15,
        "30m": _mt5.TIMEFRAME_M30,
        "60m": _mt5.TIMEFRAME_H1,
        "1h":  _mt5.TIMEFRAME_H1,
        "4h":  _mt5.TIMEFRAME_H4,
        "1d":  _mt5.TIMEFRAME_D1,
    }


class MT5Broker:
    """
    Cliente MetaTrader 5 para B3 e Forex.

    Detecta automaticamente se MT5 está disponível (Windows + lib instalada).
    Em Railway/Linux desabilita-se transparentemente — o bot continua via
    BRAPI/Yahoo Finance como fallback.
    """

    def __init__(self):
        self.login    = getattr(settings, "MT5_LOGIN", "")
        self.password = getattr(settings, "MT5_PASSWORD", "")
        self.server   = getattr(settings, "MT5_SERVER", "")
        self._connected = False
        self._available = _MT5_AVAILABLE

        global _B3_SUFFIX
        _B3_SUFFIX = getattr(settings, "MT5_SUFFIX", "")

        if _MT5_AVAILABLE:
            _load_tf_map()
            print(
                f"[mt5] Disponível (Windows) | Login: {self.login or 'N/A'} | "
                f"Server: {self.server or 'N/A'} | Keys: {'✓' if self.is_configured else '✗'}",
                flush=True,
            )
        else:
            reason = "Windows requerido" if sys.platform != "win32" else "MetaTrader5 não instalado"
            print(f"[mt5] Indisponível ({reason}) — usando fallback BRAPI/Yahoo", flush=True)

    # ── Propriedades ──────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return bool(self.login and self.password and self.server)

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def is_connected(self) -> bool:
        return self._connected

    def status(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "broker":    "MetaTrader 5",
            "available": self._available,
            "configured": self.is_configured,
            "connected": self._connected,
            "platform":  sys.platform,
        }
        if self._available and self._connected and _mt5 is not None:
            try:
                acc = _mt5.account_info()
                if acc:
                    info["account_login"]  = acc.login
                    info["account_name"]   = acc.name
                    info["balance"]        = acc.balance
                    info["equity"]         = acc.equity
                    info["currency"]       = acc.currency
                    info["server"]         = acc.server
            except Exception:
                pass
        return info

    # ── Conexão ───────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Inicializa e loga no terminal MT5. Retorna True se conectado."""
        if not self._available or _mt5 is None:
            return False
        if not self.is_configured:
            # Tenta iniciar sem login (MT5 já logado manualmente no terminal)
            ok = _mt5.initialize()
            if ok:
                self._connected = True
                ver = _mt5.version()
                print(f"[mt5] Conectado (sem login automático) | Versão: {ver}", flush=True)
            return ok

        # Inicializa com login automatico
        ok = _mt5.initialize(
            login=int(self.login),
            password=self.password,
            server=self.server,
        )
        if ok:
            self._connected = True
            acc = _mt5.account_info()
            if acc:
                print(
                    f"[mt5] Conectado ✓ | {acc.name} | Saldo: {acc.currency} {acc.balance:,.2f} | "
                    f"Server: {acc.server}",
                    flush=True,
                )
        else:
            err = _mt5.last_error()
            print(f"[mt5] Falha ao conectar: {err}", flush=True)
        return ok

    def disconnect(self):
        if self._available and _mt5 is not None:
            _mt5.shutdown()
        self._connected = False

    # ── Market Data ───────────────────────────────────────────────────────────

    def _sym(self, symbol: str) -> str:
        """Aplica sufixo da corretora se configurado (ex: PETR4 → PETR4.SA)."""
        return f"{symbol}{_B3_SUFFIX}" if _B3_SUFFIX else symbol

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """Retorna última cotação (bid/ask) de um símbolo."""
        if not self._connected or _mt5 is None:
            return None
        try:
            tick = _mt5.symbol_info_tick(self._sym(symbol))
            if tick:
                price = (tick.ask + tick.bid) / 2
                return {
                    "symbol": symbol,
                    "price":  round(price, 4),
                    "ask":    tick.ask,
                    "bid":    tick.bid,
                    "time":   datetime.fromtimestamp(tick.time).isoformat(),
                    "source": "mt5",
                }
        except Exception as e:
            print(f"[mt5] get_quote {symbol}: {e}", flush=True)
        return None

    def get_quotes_batch(self, symbols: List[str]) -> Dict[str, float]:
        """Cotações em lote — retorna {symbol: price}."""
        if not self._connected or _mt5 is None:
            return {}
        results: Dict[str, float] = {}
        for sym in symbols:
            try:
                tick = _mt5.symbol_info_tick(self._sym(sym))
                if tick:
                    price = (tick.ask + tick.bid) / 2
                    if price > 0:
                        results[sym] = round(price, 4)
            except Exception:
                pass
        return results

    def get_candles(
        self,
        symbol: str,
        interval: str = "1d",
        limit: int = 100,
    ) -> Optional[List[Dict]]:
        """Histórico de candles OHLCV via MT5."""
        if not self._connected or _mt5 is None:
            return None
        tf = _TF_MAP.get(interval)
        if tf is None:
            return None
        try:
            rates = _mt5.copy_rates_from_pos(self._sym(symbol), tf, 0, limit)
            if rates is None or len(rates) == 0:
                return None
            return [
                {
                    "timestamp": datetime.fromtimestamp(r["time"]).isoformat(),
                    "open":      float(r["open"]),
                    "high":      float(r["high"]),
                    "low":       float(r["low"]),
                    "close":     float(r["close"]),
                    "volume":    float(r["tick_volume"]),
                }
                for r in rates
            ]
        except Exception as e:
            print(f"[mt5] get_candles {symbol}: {e}", flush=True)
        return None

    # ── Ordens ────────────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,       # "buy" | "sell"
        volume: float,   # lotes MT5 (B3: 1 lote = 100 ações em geral)
        order_type: str = "market",
        price: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "daytrade_bot",
    ) -> Optional[Dict]:
        """Envia ordem ao MT5."""
        if not self._connected or _mt5 is None:
            return None

        sym_mt5 = self._sym(symbol)
        tick = _mt5.symbol_info_tick(sym_mt5)
        if not tick:
            print(f"[mt5] Símbolo não encontrado: {sym_mt5}", flush=True)
            return None

        if order_type == "market":
            trade_type = _mt5.ORDER_TYPE_BUY if side == "buy" else _mt5.ORDER_TYPE_SELL
            exec_price = tick.ask if side == "buy" else tick.bid
        else:
            trade_type = _mt5.ORDER_TYPE_BUY_LIMIT if side == "buy" else _mt5.ORDER_TYPE_SELL_LIMIT
            exec_price = price

        request = {
            "action":    _mt5.TRADE_ACTION_DEAL,
            "symbol":    sym_mt5,
            "volume":    float(volume),
            "type":      trade_type,
            "price":     exec_price,
            "sl":        sl,
            "tp":        tp,
            "deviation": 20,
            "magic":     20260311,
            "comment":   comment,
            "type_time": _mt5.ORDER_TIME_GTC,
            "type_filling": _mt5.ORDER_FILLING_FOK,
        }

        result = _mt5.order_send(request)
        if result and result.retcode == _mt5.TRADE_RETCODE_DONE:
            print(
                f"[mt5] Ordem {side.upper()} {volume} {symbol} executada | "
                f"Ticket: {result.order} | Price: {result.price}",
                flush=True,
            )
            return {
                "ticket":  result.order,
                "symbol":  symbol,
                "side":    side,
                "volume":  volume,
                "price":   result.price,
                "retcode": result.retcode,
            }
        else:
            code = result.retcode if result else "N/A"
            print(f"[mt5] Falha ordem {symbol}: retcode={code}", flush=True)
        return None

    def get_positions(self) -> List[Dict]:
        """Lista posições abertas no MT5."""
        if not self._connected or _mt5 is None:
            return []
        try:
            positions = _mt5.positions_get()
            if positions is None:
                return []
            return [
                {
                    "ticket":  p.ticket,
                    "symbol":  p.symbol.replace(_B3_SUFFIX, ""),
                    "type":    "buy" if p.type == 0 else "sell",
                    "volume":  p.volume,
                    "open_price": p.price_open,
                    "current_price": p.price_current,
                    "profit": p.profit,
                    "sl": p.sl,
                    "tp": p.tp,
                }
                for p in positions
            ]
        except Exception as e:
            print(f"[mt5] get_positions: {e}", flush=True)
        return []

    def close_position(self, ticket: int) -> bool:
        """Fecha posição pelo ticket."""
        if not self._connected or _mt5 is None:
            return False
        try:
            pos = _mt5.positions_get(ticket=ticket)
            if not pos:
                return False
            p = pos[0]
            sym_mt5 = p.symbol
            tick = _mt5.symbol_info_tick(sym_mt5)
            close_price = tick.bid if p.type == 0 else tick.ask
            close_type  = _mt5.ORDER_TYPE_SELL if p.type == 0 else _mt5.ORDER_TYPE_BUY

            request = {
                "action":    _mt5.TRADE_ACTION_DEAL,
                "symbol":    sym_mt5,
                "volume":    p.volume,
                "type":      close_type,
                "position":  ticket,
                "price":     close_price,
                "deviation": 20,
                "magic":     20260311,
                "comment":   "close_daytrade_bot",
                "type_time": _mt5.ORDER_TIME_GTC,
                "type_filling": _mt5.ORDER_FILLING_FOK,
            }
            result = _mt5.order_send(request)
            return bool(result and result.retcode == _mt5.TRADE_RETCODE_DONE)
        except Exception as e:
            print(f"[mt5] close_position {ticket}: {e}", flush=True)
        return False
