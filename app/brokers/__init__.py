"""
Módulo de Brokers — conexão com corretoras e exchanges reais.

Brokers disponíveis:
  - BTG Pactual  → B3 (ações brasileiras) — market data + ordens
  - Binance Auth → Crypto (BTC, ETH...) — dados RT + ordens assinadas
  - Alpha Vantage → US stocks, forex, commodities — dados alternativos
"""

from app.brokers.btg import BTGBroker
from app.brokers.binance_auth import BinanceAuthBroker
from app.brokers.alpha_vantage import AlphaVantageBroker

__all__ = ["BTGBroker", "BinanceAuthBroker", "AlphaVantageBroker"]
