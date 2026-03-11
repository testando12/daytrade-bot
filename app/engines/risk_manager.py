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

# ── Mapeamento de setores ────────────────────────────────────────────────────
# Limita exposição máxima do portfolio por setor.
# Evita que uma crise sectorial (ex: crash crypto, selloff B3) destrua o capital.
_SECTOR_MAP: Dict[str, List[str]] = {
    "CRYPTO":      ["BTC","ETH","BNB","SOL","XRP","ADA","AVAX","DOT","LINK",
                    "DOGE","SHIB","PEPE","WIF","FLOKI","BONK",
                    "AAVE","MKR","COMP","CRV","SNX","UNI",
                    "JTO","PYTH","JUP","POPCAT","RENDER","FET",
                    "NEAR","APT","ARB","OP","INJ","SUI","SEI","TIA","STRK","MANTA","TAO"],
    "B3":          ["PETR4","VALE3","ITUB4","BBDC4","BBAS3","ITSA4",
                    "GGBR4","JBSS3","SUZB3","PRIO3","CSAN3","EGIE3",
                    "MXRF11","XPML11","VISC11","HGLG11","KNRI11","XPLG11",
                    "BCFF11","RBRF11","IRDM11","KNCR11"],
    "US_STOCKS":   ["AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA",
                    "AMD","INTC","QCOM","AVGO","MU","SOXL",
                    "JPM","GS","BAC","WFC","MS",
                    "JNJ","PFE","MRK","ABT",
                    "XOM","CVX","COP","SLB"],
    "INTL_ETFS":   ["EFA","EEM","VGK","EWG","EWQ","EWU","EWJ","EWY",
                    "ASHR","INDA","EWA","EWZ"],
    "COMMODITIES": ["GOLD","SILVER","GLD","SLV","OIL","NATGAS",
                    "CAFE","SOJA","MILHO","ACUCAR","TRIGO","CACAU","COBRE"],
    "FOREX":       ["USDBRL","EURUSD","GBPUSD","USDJPY","EURBRLL",
                    "DXY","AUDUSD","USDMXN","USDCLP"],
}

# Máxima exposição (% do capital) por setor
_SECTOR_MAX_PCT: Dict[str, float] = {
    "CRYPTO":     0.40,   # 40% — volatilidade extrema
    "B3":         0.35,   # 35% — risco Brasil / taxa Selic
    "US_STOCKS":  0.35,   # 35%
    "INTL_ETFS":  0.20,   # 20%
    "COMMODITIES":0.20,   # 20%
    "FOREX":      0.15,   # 15%
}

# Mapa reverso: ativo (maiúsculo) → setor
_ASSET_TO_SECTOR: Dict[str, str] = {
    asset.upper(): sector
    for sector, assets in _SECTOR_MAP.items()
    for asset in assets
}


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
    # 4. SECTOR EXPOSURE CAP
    # ─────────────────────────────────────────

    def _get_sector(self, asset: str) -> Optional[str]:
        """Retorna o setor do ativo ou None se não mapeado."""
        return _ASSET_TO_SECTOR.get(asset.upper())

    def check_sector_cap(
        self,
        asset: str,
        trade_amount: float,
        capital: float,
    ) -> Tuple[bool, str]:
        """
        Verifica se adicionar ``trade_amount`` ao setor do ``asset`` ultrapassa
        o limite máximo de exposição setorial.

        Args:
            asset:        Símbolo do ativo (ex: "BTC", "PETR4")
            trade_amount: Valor em R$/USD da operação pretendida
            capital:      Capital total de referência

        Returns:
            (allowed: bool, mensagem)
        """
        if capital <= 0:
            return True, "OK"

        sector = self._get_sector(asset)
        if sector is None:
            return True, "OK"  # ativo não mapeado → sem restrição setorial

        # Exposição atual no setor (soma dos amounts das posições abertas)
        current_exposure = sum(
            pos["amount"]
            for a, pos in self.positions.items()
            if self._get_sector(a) == sector
        )

        max_allowed = capital * _SECTOR_MAX_PCT.get(sector, 0.35)
        if current_exposure + trade_amount > max_allowed:
            pct_now = current_exposure / capital * 100
            pct_max = _SECTOR_MAX_PCT.get(sector, 0.35) * 100
            return (
                False,
                f"Setor {sector} com {pct_now:.0f}% do capital "
                f"(limite {pct_max:.0f}%) — operação bloqueada",
            )

        return True, "OK"

    # ─────────────────────────────────────────
    # 5. VALIDAÇÃO PRÉ-TRADE (COMBINA TUDO)
    # ─────────────────────────────────────────

    def can_trade(
        self,
        asset: Optional[str] = None,
        trade_amount: float = 0.0,
        capital: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        Verifica TODAS as condições antes de permitir um trade.

        Args:
            asset:        Símbolo do ativo (opcional — ativa sector cap)
            trade_amount: Valor da operação (opcional)
            capital:      Capital total (opcional)

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

        # 4. Sector exposure cap (apenas quando informado)
        if asset and trade_amount > 0 and capital > 0:
            ok, msg = self.check_sector_cap(asset, trade_amount, capital)
            if not ok:
                return False, msg

        return True, "OK"

    def record_trade(self, asset: str, action: str, price: float, amount: float):
        """Registra um trade executado"""
        record = TradeRecord(asset, action, price, amount)
        self.trade_history.append(record)

        if action == "BUY":
            self.register_position(asset, price, amount)

    # ─────────────────────────────────────────
    # 6. STATUS E RELATÓRIOS
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
