"""
Modelos de banco de dados SQLAlchemy
"""

from sqlalchemy import Column, Integer, Float, String, DateTime, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()


class Portfolio(Base):
    """Modelo de portfólio do usuário"""

    __tablename__ = "portfolios"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    name = Column(String)
    description = Column(Text, nullable=True)
    total_capital = Column(Float)
    current_balance = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)


class Position(Base):
    """Modelo de posição aberta no portfólio"""

    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(Integer, index=True)
    asset = Column(String, index=True)
    quantity = Column(Float)
    entry_price = Column(Float)
    current_price = Column(Float)
    allocated_amount = Column(Float)
    current_value = Column(Float)
    unrealized_profit_loss = Column(Float)
    entered_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)


class Trade(Base):
    """Modelo de histórico de trades executados"""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(Integer, index=True)
    asset = Column(String, index=True)
    trade_type = Column(String)  # BUY, SELL
    quantity = Column(Float)
    price = Column(Float)
    total_value = Column(Float)
    reason = Column(String)  # Razão da operação
    momentum_score = Column(Float, nullable=True)
    irq_score = Column(Float, nullable=True)
    executed_at = Column(DateTime, default=datetime.utcnow)


class MarketData(Base):
    """Modelo de dados históricos do mercado"""

    __tablename__ = "market_data"

    id = Column(Integer, primary_key=True, index=True)
    asset = Column(String, index=True)
    timestamp = Column(DateTime, index=True)
    price = Column(Float)
    volume = Column(Float)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    open = Column(Float, nullable=True)
    close = Column(Float, nullable=True)


class Analysis(Base):
    """Modelo de resultados de análise"""

    __tablename__ = "analysis"

    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(Integer, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    asset = Column(String, index=True)
    momentum_score = Column(Float)
    momentum_classification = Column(String)
    irq_score = Column(Float)
    irq_level = Column(String)
    recommended_allocation = Column(Float)
    current_allocation = Column(Float)
    analysis_data = Column(Text)  # JSON string com dados detalhados
