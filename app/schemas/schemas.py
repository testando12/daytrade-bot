"""
Schemas Pydantic para validação de dados
"""

from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime


# Portfolio Schemas
class PortfolioBase(BaseModel):
    name: str
    description: Optional[str] = None
    total_capital: float
    user_id: str


class PortfolioCreate(PortfolioBase):
    pass


class PortfolioUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    total_capital: Optional[float] = None
    current_balance: Optional[float] = None


class Portfolio(PortfolioBase):
    id: int
    current_balance: float
    created_at: datetime
    updated_at: datetime
    is_active: bool

    class Config:
        from_attributes = True


# Position Schemas
class PositionBase(BaseModel):
    asset: str
    quantity: float
    entry_price: float


class PositionCreate(PositionBase):
    portfolio_id: int


class Position(PositionBase):
    id: int
    portfolio_id: int
    current_price: float
    allocated_amount: float
    current_value: float
    unrealized_profit_loss: float
    entered_at: datetime
    updated_at: datetime
    is_active: bool

    class Config:
        from_attributes = True


# Trade Schemas
class TradeBase(BaseModel):
    asset: str
    trade_type: str  # BUY, SELL
    quantity: float
    price: float
    total_value: float
    reason: str


class TradeCreate(TradeBase):
    portfolio_id: int
    momentum_score: Optional[float] = None
    irq_score: Optional[float] = None


class Trade(TradeBase):
    id: int
    portfolio_id: int
    executed_at: datetime

    class Config:
        from_attributes = True


# Market Data Schemas
class MarketDataBase(BaseModel):
    asset: str
    timestamp: datetime
    price: float
    volume: float
    high: Optional[float] = None
    low: Optional[float] = None
    open: Optional[float] = None
    close: Optional[float] = None


class MarketDataCreate(MarketDataBase):
    pass


class MarketData(MarketDataBase):
    id: int

    class Config:
        from_attributes = True


# Analysis Schemas
class MomentumAnalysisResult(BaseModel):
    asset: str
    momentum_score: float
    trend_status: str
    classification: str
    return_pct: float
    ma_short: float
    ma_long: float


class RiskAnalysisResult(BaseModel):
    irq_score: float
    level: str
    s1_trend_loss: float
    s2_selling_pressure: float
    s3_volatility: float
    s4_rsi_divergence: float
    s5_losing_streak: float
    rsi: float


class AllocationResult(BaseModel):
    asset: str
    classification: str
    current_amount: float
    recommended_amount: float
    action: str
    change_percentage: float


class AnalysisReport(BaseModel):
    timestamp: datetime
    portfolio_id: int
    momentum_analysis: Dict[str, MomentumAnalysisResult]
    risk_analysis: RiskAnalysisResult
    allocations: Dict[str, AllocationResult]
    risk_metrics: Dict[str, float]


# API Response Schemas
class ApiResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict] = None
    error: Optional[str] = None


class BotStatus(BaseModel):
    portfolio_id: int
    is_running: bool
    last_analysis: datetime
    total_capital: float
    current_balance: float
    cash_available: float
    active_positions: int
    total_unrealized_pnl: float
