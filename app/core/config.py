"""
Configurações da aplicação Day Trade Bot
"""

import os
from typing import Optional, List


class Settings:
    """Configurações globais da aplicação"""

    # Aplicação
    APP_NAME: str = "Day Trade Bot"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"

    # Mercado
    MARKET_INTERVALS: List[int] = [5, 15, 60]  # minutos
    MARKET_API_TIMEOUT: int = 10  # segundos
    PREFERRED_MARKET: str = "binance"  # binance ou polygon

    # Bot - Configurações de trading
    INITIAL_CAPITAL: float = float(os.getenv("INITIAL_CAPITAL", "150"))
    MAX_POSITION_PERCENTAGE: float = 0.30  # máximo 30% por ativo
    MIN_POSITION_AMOUNT: float = 10.0  # alocação mínima por ativo
    STOP_LOSS_PERCENTAGE: float = 0.05  # 5%
    TAKE_PROFIT_PERCENTAGE: float = 0.10  # 10%
    REBALANCE_INTERVAL: int = 300  # segundos (5 minutos)

    # Limites operacionais (proteção obrigatória - spec custo.md)
    MAX_DAILY_LOSS_PERCENTAGE: float = 0.10  # 10% perda máxima diária
    MAX_TRADES_PER_HOUR: int = 20  # limite de operações por hora
    MAX_TRADES_PER_DAY: int = 100  # limite diário

    # Banco de Dados
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./data/daytrade.db")

    # API de Mercado
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")
    BINANCE_BASE_URL: str = "https://api.binance.com"

    # BRAPI — API B3 brasileira (brapi.dev)
    # Sem token: funciona para PETR4, MGLU3, VALE3, ITUB4 (plano gratuito)
    # Com token: todos os ativos, atualização a cada 5-30min conforme plano
    # Obtenha grátis em: https://brapi.dev/dashboard
    BRAPI_TOKEN: str = os.getenv("BRAPI_TOKEN", "")

    # Alertas
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")

    # Proteção Global (IRQ - Índice de Risco de Queda)
    IRQ_THRESHOLD_HIGH: float = 0.70  # 70% - começar redução
    IRQ_THRESHOLD_VERY_HIGH: float = 0.80  # 80% - redução grande
    IRQ_THRESHOLD_CRITICAL: float = 0.90  # 90% - sair do mercado
    IRQ_REDUCTION_MODERATE: float = 0.40  # reduzir 40% das posições
    IRQ_REDUCTION_HIGH: float = 0.70  # reduzir 70% das posições

    # Momentum Score - Pesos
    MOMENTUM_WEIGHT_RETURN: float = 0.50
    MOMENTUM_WEIGHT_TREND: float = 0.30
    MOMENTUM_WEIGHT_VOLUME: float = 0.20

    # Ativos permitidos — ações da B3 (Bovespa)
    ALLOWED_ASSETS: List[str] = [
        # Petróleo & Energia
        "PETR4", "PRIO3", "CSAN3", "EGIE3",
        # Mineração & Siderurgia
        "VALE3", "GGBR4",
        # Bancos & Finanças
        "ITUB4", "BBDC4", "BBAS3", "ITSA4",
        # Consumo & Varejo
        "ABEV3", "MGLU3", "LREN3",
        # Indústria & Tecnologia
        "WEGE3", "EMBR3",
        # Logística & Locação
        "RENT3",
        # Alimentos
        "JBSS3",
        # Papel & Celulose
        "SUZB3",
        # Telecom
        "VIVT3",
        # Saúde
        "RDOR3",
    ]

    # Criptomoedas (Yahoo Finance: BTC-USD, ETH-USD, ...)
    CRYPTO_ASSETS: List[str] = [
        "BTC", "ETH", "BNB", "SOL", "ADA",
        "XRP", "DOGE", "AVAX", "DOT", "LINK",
    ]

    # Todos os ativos (B3 + Crypto)
    @property
    def ALL_ASSETS(self) -> List[str]:
        return self.ALLOWED_ASSETS + self.CRYPTO_ASSETS

    # Logs
    LOG_LEVEL: str = "INFO"


settings = Settings()
