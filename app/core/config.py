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
    INITIAL_CAPITAL: float = float(os.getenv("INITIAL_CAPITAL", "2000"))
    MAX_POSITION_PERCENTAGE: float = 0.30  # máximo 30% por ativo
    MIN_POSITION_AMOUNT: float = 10.0  # alocação mínima por ativo
    STOP_LOSS_PERCENTAGE: float = 0.02  # 2% — rápido e agressivo
    TAKE_PROFIT_PERCENTAGE: float = 0.015  # 1.5% — realiza cedo
    TRAILING_STOP_PERCENTAGE: float = 0.012  # 1.2% abaixo do pico — protege lucro sem cortar cedo
    REBALANCE_INTERVAL: int = 300  # segundos (5 minutos)

    # Limites operacionais (proteção inteligente — agressiva mas com pausa/retorno)
    MAX_DAILY_LOSS_PERCENTAGE: float = 0.08  # 8% perda diária → PAUSA (não trava, só pausa)
    MAX_WEEKLY_LOSS_PERCENTAGE: float = 0.15  # 15% semanal → opera 25% do tamanho
    MAX_DRAWDOWN_PERCENTAGE: float = 0.40  # 40% do capital inicial → HARD STOP (piso absoluto)
    RESUME_MOMENTUM_THRESHOLD: float = 0.60  # momentum > 0.60 → volta a operar após pausa
    CONSECUTIVE_LOSS_REDUCE: int = 3  # após 3 perdas seguidas, reduz tamanho 50%
    CONSECUTIVE_LOSS_RECOVERY: float = 0.50  # fator de redução após perdas consecutivas
    MAX_TRADES_PER_HOUR: int = 60  # mais trades com scalping
    MAX_TRADES_PER_DAY: int = 500  # mais trades com ciclos rápidos

    # Filtro de score mínimo (só opera se momentum > threshold)
    MIN_MOMENTUM_SCORE: float = float(os.getenv("MIN_MOMENTUM_SCORE", "0.35"))

    # Kelly Criterion — multiplier conservador
    KELLY_FRACTION: float = 0.25  # usa 25% do Kelly real (Kelly fracionário)

    # Compounding: reinveste % do lucro diário (0.0 = não, 1.0 = 100%)
    COMPOUNDING_RATE: float = float(os.getenv("COMPOUNDING_RATE", "1.0"))

    # Ciclos rápidos: intervalo em minutos para crypto fora do horário B3
    CRYPTO_CYCLE_MINUTES: int = int(os.getenv("CRYPTO_CYCLE_MINUTES", "10"))
    B3_CYCLE_MINUTES: int = int(os.getenv("B3_CYCLE_MINUTES", "30"))

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

    # Criptomoedas (Binance: BTC, ETH, ...) — 30 assets 24h
    CRYPTO_ASSETS: List[str] = [
        # Top 10 (originais)
        "BTC", "ETH", "BNB", "SOL", "ADA",
        "XRP", "DOGE", "AVAX", "DOT", "LINK",
        # Altcoins voláteis (NOVAS)
        "MATIC", "SHIB", "UNI", "LTC", "ATOM",
        "FIL", "NEAR", "APT", "ARB", "OP",
        "INJ", "SUI", "SEI", "TIA", "PEPE",
        "WIF", "FLOKI", "BONK", "RENDER", "FET",
    ]

    # Forex (via Yahoo Finance: EURUSD=X, etc.)
    FOREX_PAIRS: List[str] = [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
        "USDCAD", "USDCHF", "NZDUSD", "EURGBP",
    ]

    # Commodities (via Yahoo Finance: GC=F gold, SI=F silver, CL=F oil)
    COMMODITIES: List[str] = [
        "GOLD", "SILVER", "OIL", "NATGAS",
    ]

    # Ações dos EUA — NYSE / NASDAQ (Yahoo Finance sem sufixo)
    US_STOCKS: List[str] = [
        # Big Tech
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
        # Semicondutores & Hardware
        "AMD", "INTC", "QCOM", "AVGO", "MU",
        # Software & Cloud
        "ORCL", "CRM", "ADBE", "CSCO", "IBM",
        # Internet & Serviços
        "NFLX", "PYPL", "SNAP", "UBER", "LYFT",
        # Finanças
        "JPM", "BAC", "GS", "V", "MA", "WFC", "AXP",
        # Saúde & Farma
        "JNJ", "PFE", "UNH", "ABBV", "MRK", "CVS",
        # Consumo & Varejo
        "DIS", "SBUX", "NKE", "WMT", "COST", "TGT", "HD",
        # Energia
        "XOM", "CVX", "COP",
        # Telecomunicações
        "T", "VZ",
        # Outros
        "KO", "PEP", "MCD",
    ]

    # Todos os ativos (B3 + US + Crypto + Forex + Commodities)
    @property
    def ALL_ASSETS(self) -> List[str]:
        return self.ALLOWED_ASSETS + self.US_STOCKS + self.CRYPTO_ASSETS + self.FOREX_PAIRS + self.COMMODITIES

    # Logs
    LOG_LEVEL: str = "INFO"


settings = Settings()
