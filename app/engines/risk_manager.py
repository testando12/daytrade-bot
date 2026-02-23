"""
Gerenciador de Risco Operacional
Implementa: Stop Loss, Take Profit, Limites Diários/Hora

Baseado na spec (custo.md / prevenção.md):
- máximo 30% do capital em um ativo ✅ (portfolio.py)
- stop loss de 5% a 10% ✅ (este módulo)
- limite diário de perda ✅ (este módulo)
- limite de operações por hora ✅ (este módulo)
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from app.core.config import settings


class TradeRecord:
    """Registro de operação executada"""

    def __init__(self, asset: str, action: str, price: float, amount: float):
        self.asset = asset
        self.action = action  # BUY, SELL
        self.price = price
        self.amount = amount
        self.timestamp = datetime.now()
        self.pnl = 0.0


class RiskManager:
    """
    Gerenciador de risco operacional.
    
    Responsabilidades:
    1. Stop Loss: encerra posição se perda > X%
    2. Take Profit: encerra posição se ganho > Y%
    3. Limite diário de perda: bloqueia operações se perda acumulada > Z%
    4. Limite de trades por hora/dia: evita overtrading
    """

    def __init__(self):
        self.positions: Dict[str, Dict] = {}  # {asset: {entry_price, amount, timestamp}}
        self.trade_history: List[TradeRecord] = []
        self.daily_pnl: float = 0.0
        self.daily_pnl_reset_date: str = datetime.now().strftime("%Y-%m-%d")
        self.is_locked: bool = False  # Trava de emergência
        self.lock_reason: str = ""

    def _reset_daily_if_needed(self):
        """Reseta contadores diários à meia-noite"""
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self.daily_pnl_reset_date:
            self.daily_pnl = 0.0
            self.daily_pnl_reset_date = today
            self.is_locked = False
            self.lock_reason = ""

    # ─────────────────────────────────────────
    # 1. STOP LOSS / TAKE PROFIT
    # ─────────────────────────────────────────

    def register_position(self, asset: str, entry_price: float, amount: float):
        """Registra uma posição aberta"""
        self.positions[asset] = {
            "entry_price": entry_price,
            "amount": amount,
            "timestamp": datetime.now(),
            "highest_price": entry_price,  # Para trailing stop
        }

    def check_stop_loss(self, asset: str, current_price: float) -> Dict:
        """
        Verifica se o stop loss foi atingido para um ativo.
        
        Returns:
            Dict com trigger=True/False, action, loss_pct
        """
        if asset not in self.positions:
            return {"triggered": False, "asset": asset}

        pos = self.positions[asset]
        entry_price = pos["entry_price"]

        if entry_price == 0:
            return {"triggered": False, "asset": asset}

        change_pct = (current_price - entry_price) / entry_price

        # Atualizar highest price (para trailing stop futuro)
        if current_price > pos["highest_price"]:
            pos["highest_price"] = current_price

        # STOP LOSS: perda > stop_loss_percentage
        if change_pct <= -settings.STOP_LOSS_PERCENTAGE:
            return {
                "triggered": True,
                "asset": asset,
                "action": "STOP_LOSS",
                "entry_price": entry_price,
                "current_price": current_price,
                "loss_pct": change_pct * 100,
                "amount": pos["amount"],
                "message": f"🛑 STOP LOSS {asset}: {change_pct*100:.2f}% (limite: {settings.STOP_LOSS_PERCENTAGE*100:.0f}%)",
            }

        # TAKE PROFIT: ganho > take_profit_percentage
        if change_pct >= settings.TAKE_PROFIT_PERCENTAGE:
            return {
                "triggered": True,
                "asset": asset,
                "action": "TAKE_PROFIT",
                "entry_price": entry_price,
                "current_price": current_price,
                "gain_pct": change_pct * 100,
                "amount": pos["amount"],
                "message": f"💰 TAKE PROFIT {asset}: +{change_pct*100:.2f}% (limite: {settings.TAKE_PROFIT_PERCENTAGE*100:.0f}%)",
            }

        return {
            "triggered": False,
            "asset": asset,
            "change_pct": change_pct * 100,
            "distance_to_stop": (change_pct + settings.STOP_LOSS_PERCENTAGE) * 100,
            "distance_to_profit": (settings.TAKE_PROFIT_PERCENTAGE - change_pct) * 100,
        }

    def check_all_positions(self, current_prices: Dict[str, float]) -> List[Dict]:
        """Verifica stop loss/take profit de todas as posições"""
        alerts = []
        for asset, price in current_prices.items():
            result = self.check_stop_loss(asset, price)
            if result.get("triggered"):
                alerts.append(result)
        return alerts

    def close_position(self, asset: str, exit_price: float) -> Optional[Dict]:
        """Fecha uma posição e registra o P&L"""
        if asset not in self.positions:
            return None

        pos = self.positions[asset]
        entry_price = pos["entry_price"]
        amount = pos["amount"]
        pnl = ((exit_price - entry_price) / entry_price) * amount if entry_price > 0 else 0

        # Registrar trade
        record = TradeRecord(asset, "SELL", exit_price, amount)
        record.pnl = pnl
        self.trade_history.append(record)

        # Atualizar P&L diário
        self._reset_daily_if_needed()
        self.daily_pnl += pnl

        # Remover posição
        del self.positions[asset]

        return {
            "asset": asset,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "amount": amount,
            "pnl": pnl,
            "pnl_pct": ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0,
        }

    # ─────────────────────────────────────────
    # 2. LIMITES DIÁRIOS
    # ─────────────────────────────────────────

    def check_daily_loss_limit(self) -> Dict:
        """
        Verifica se o limite de perda diária foi atingido.
        Se sim, bloqueia novas operações.
        """
        self._reset_daily_if_needed()

        max_daily_loss = settings.INITIAL_CAPITAL * settings.MAX_DAILY_LOSS_PERCENTAGE
        
        if self.daily_pnl <= -max_daily_loss:
            self.is_locked = True
            self.lock_reason = f"Perda diária de R${abs(self.daily_pnl):.2f} excedeu limite de R${max_daily_loss:.2f}"
            return {
                "locked": True,
                "reason": self.lock_reason,
                "daily_pnl": self.daily_pnl,
                "max_loss": -max_daily_loss,
            }

        return {
            "locked": False,
            "daily_pnl": self.daily_pnl,
            "max_loss": -max_daily_loss,
            "remaining": max_daily_loss + self.daily_pnl,
        }

    # ─────────────────────────────────────────
    # 3. LIMITES DE OPERAÇÕES (ANTI-OVERTRADING)
    # ─────────────────────────────────────────

    def _count_trades_in_window(self, minutes: int) -> int:
        """Conta trades nos últimos N minutos"""
        cutoff = datetime.now() - timedelta(minutes=minutes)
        return sum(1 for t in self.trade_history if t.timestamp >= cutoff)

    def check_trade_limits(self) -> Dict:
        """
        Verifica se os limites de operações foram atingidos.
        
        Returns:
            Dict com allowed=True/False e motivo
        """
        self._reset_daily_if_needed()

        trades_last_hour = self._count_trades_in_window(60)
        trades_today = self._count_trades_in_window(24 * 60)

        if trades_last_hour >= settings.MAX_TRADES_PER_HOUR:
            return {
                "allowed": False,
                "reason": f"Limite de {settings.MAX_TRADES_PER_HOUR} trades/hora atingido ({trades_last_hour})",
                "trades_last_hour": trades_last_hour,
                "trades_today": trades_today,
            }

        if trades_today >= settings.MAX_TRADES_PER_DAY:
            return {
                "allowed": False,
                "reason": f"Limite de {settings.MAX_TRADES_PER_DAY} trades/dia atingido ({trades_today})",
                "trades_last_hour": trades_last_hour,
                "trades_today": trades_today,
            }

        return {
            "allowed": True,
            "trades_last_hour": trades_last_hour,
            "trades_today": trades_today,
            "remaining_hour": settings.MAX_TRADES_PER_HOUR - trades_last_hour,
            "remaining_day": settings.MAX_TRADES_PER_DAY - trades_today,
        }

    # ─────────────────────────────────────────
    # 4. VALIDAÇÃO PRÉ-TRADE (COMBINA TUDO)
    # ─────────────────────────────────────────

    def can_trade(self) -> Tuple[bool, str]:
        """
        Verifica TODAS as condições antes de permitir um trade.
        
        Returns:
            (allowed: bool, reason: str)
        """
        # 1. Trava de emergência
        if self.is_locked:
            return False, f"Sistema travado: {self.lock_reason}"

        # 2. Limite de perda diária
        daily_check = self.check_daily_loss_limit()
        if daily_check["locked"]:
            return False, daily_check["reason"]

        # 3. Limite de operações
        trade_check = self.check_trade_limits()
        if not trade_check["allowed"]:
            return False, trade_check["reason"]

        return True, "OK"

    def record_trade(self, asset: str, action: str, price: float, amount: float):
        """Registra um trade executado"""
        record = TradeRecord(asset, action, price, amount)
        self.trade_history.append(record)

        if action == "BUY":
            self.register_position(asset, price, amount)

    # ─────────────────────────────────────────
    # 5. STATUS E RELATÓRIOS
    # ─────────────────────────────────────────

    def get_status(self) -> Dict:
        """Retorna status completo do gerenciador de risco"""
        self._reset_daily_if_needed()

        daily_check = self.check_daily_loss_limit()
        trade_check = self.check_trade_limits()

        positions_status = {}
        for asset, pos in self.positions.items():
            positions_status[asset] = {
                "entry_price": pos["entry_price"],
                "amount": pos["amount"],
                "highest_price": pos["highest_price"],
                "timestamp": pos["timestamp"].isoformat(),
            }

        return {
            "is_locked": self.is_locked,
            "lock_reason": self.lock_reason,
            "daily_pnl": self.daily_pnl,
            "daily_loss_limit": daily_check,
            "trade_limits": trade_check,
            "open_positions": len(self.positions),
            "positions": positions_status,
            "total_trades_today": self._count_trades_in_window(24 * 60),
            "stop_loss_pct": settings.STOP_LOSS_PERCENTAGE * 100,
            "take_profit_pct": settings.TAKE_PROFIT_PERCENTAGE * 100,
        }


# Instância global
risk_manager = RiskManager()
