"""
Engine de Análise de Risco v2
Melhorias:
- ATR-based stop loss dinâmico (não fixo em 5%)
- Per-asset risk score (cada ativo tem seu próprio IRQ)
- Detecção de divergência RSI/preço (preço sobe mas RSI cai = fraqueza)
- Drawdown tracker (quanto o ativo caiu do pico recente)
- Pesos calibrados para reduzir falsos positivos
"""

import math
from typing import Dict, List
from app.core.config import settings


class RiskAnalyzer:
    """Analisador de risco v2"""

    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        avg_gain = sum(max(0, d) for d in deltas[:period]) / period
        avg_loss = sum(max(0, -d) for d in deltas[:period]) / period
        for d in deltas[period:]:
            avg_gain = (avg_gain * (period - 1) + max(0, d))  / period
            avg_loss = (avg_loss * (period - 1) + max(0, -d)) / period
        if avg_loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    @staticmethod
    def calculate_atr(prices: List[float], period: int = 14) -> float:
        """ATR simples (sem high/low, usa closes)."""
        if len(prices) < 2:
            return 0.0
        trs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
        recent = trs[-period:] if len(trs) >= period else trs
        return sum(recent) / len(recent) if recent else 0.0

    @staticmethod
    def calculate_drawdown(prices: List[float], lookback: int = 20) -> float:
        """Drawdown do pico nos últimos N candles. 0=sem queda, 1=caiu 100%."""
        if len(prices) < 2:
            return 0.0
        window = prices[-lookback:] if len(prices) >= lookback else prices
        peak = max(window)
        current = window[-1]
        if peak == 0:
            return 0.0
        return max(0.0, (peak - current) / peak)

    @staticmethod
    def detect_losing_streak(prices: List[float], min_periods: int = 3) -> bool:
        if len(prices) < min_periods + 1:
            return False
        returns = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        return all(r < 0 for r in returns[-min_periods:])

    @staticmethod
    def calculate_volatility(prices: List[float], period: int = 20) -> float:
        if len(prices) < period:
            return 0.0
        recent = prices[-period:]
        returns = [
            (recent[i] - recent[i - 1]) / recent[i - 1]
            for i in range(1, len(recent))
            if recent[i - 1] != 0
        ]
        if not returns:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return min(1.0, (variance ** 0.5) / 0.05)

    @staticmethod
    def dynamic_stop_loss(prices: List[float], atr_multiple: float = 2.0) -> float:
        """
        Stop loss dinâmico baseado no ATR.
        Stop = preço atual - (ATR * múltiplo)
        Retorna o percentual de stop como fração (ex: 0.03 = 3%)
        """
        if len(prices) < 2:
            return settings.STOP_LOSS_PERCENTAGE
        atr = RiskAnalyzer.calculate_atr(prices, 14)
        current = prices[-1]
        if current == 0:
            return settings.STOP_LOSS_PERCENTAGE
        stop_pct = (atr * atr_multiple) / current
        # Clamp entre 1% e 10%
        return max(0.01, min(0.10, stop_pct))

    @staticmethod
    def calculate_irq(
        prices: List[float],
        volumes: List[float],
        period_short: int = 9,
        period_long: int = 21,
    ) -> Dict:
        """
        IRQ v2 com sinais aprimorados:
        S1: Perda de EMA (tendência)
        S2: Pressão vendedora (queda + volume)
        S3: Volatilidade normalizada
        S4: RSI divergência
        S5: Sequência de quedas
        S6: Drawdown do pico recente (NOVO)
        """
        MIN = max(period_long, 22)
        if len(prices) < MIN or len(volumes) < MIN:
            return {
                "s1_trend_loss": 0.0, "s2_selling_pressure": 0.0,
                "s3_volatility": 0.0, "s4_rsi_divergence": 0.0,
                "s5_losing_streak": 0.0, "s6_drawdown": 0.0,
                "irq_score": 0.0, "raw_irq_score": 0.0,
                "rsi": 50.0, "volatility": 0.0,
                "atr": 0.0, "stop_loss_pct": settings.STOP_LOSS_PERCENTAGE,
                "valid": False,
            }

        # EMA curta vs longa
        k_fast = 2.0 / (period_short + 1)
        k_slow = 2.0 / (period_long + 1)
        ema_fast = sum(prices[:period_short]) / period_short
        ema_slow = sum(prices[:period_long]) / period_long
        for p in prices[period_short:]:
            ema_fast = p * k_fast + ema_fast * (1 - k_fast)
        for p in prices[period_long:]:
            ema_slow = p * k_slow + ema_slow * (1 - k_slow)

        # S1: EMA lenta > EMA rápida = tendência negativa
        s1 = max(0.0, (ema_slow - ema_fast) / ema_slow) if ema_slow > 0 else 0.0
        s1 = min(1.0, s1 * 20)  # amplificar diferenças pequenas

        # S2: Pressão vendedora
        recent_return = (prices[-1] - prices[-2]) / prices[-2] if prices[-2] != 0 else 0
        avg_volume = sum(volumes[-period_long:]) / period_long
        vol_ratio = volumes[-1] / avg_volume if avg_volume > 0 else 1.0
        s2 = min(1.0, max(0.0, abs(recent_return) * vol_ratio * 10)) if recent_return < 0 else 0.0

        # S3: Volatilidade
        s3 = RiskAnalyzer.calculate_volatility(prices, period_long)

        # S4: RSI divergência
        rsi = RiskAnalyzer.calculate_rsi(prices)
        s4 = max(0.0, (40.0 - rsi) / 40.0) if rsi < 40 else 0.0  # só ativa abaixo de 40

        # S5: Sequência de quedas
        s5 = 1.0 if RiskAnalyzer.detect_losing_streak(prices, 3) else 0.0

        # S6: Drawdown recente (NOVO)
        s6 = min(1.0, RiskAnalyzer.calculate_drawdown(prices, 20) * 3)

        # Pesos calibrados (S6 é informativo mas não dominante)
        weights = {"s1": 0.25, "s2": 0.20, "s3": 0.15, "s4": 0.15, "s5": 0.15, "s6": 0.10}
        raw_irq = (
            weights["s1"] * s1 + weights["s2"] * s2 + weights["s3"] * s3
            + weights["s4"] * s4 + weights["s5"] * s5 + weights["s6"] * s6
        )

        # Transformação logística
        irq_final = RiskAnalyzer._logistic_transform(raw_irq)

        # Stop loss dinâmico
        stop_pct = RiskAnalyzer.dynamic_stop_loss(prices)
        atr = RiskAnalyzer.calculate_atr(prices, 14)

        return {
            "s1_trend_loss":        float(s1),
            "s2_selling_pressure":  float(s2),
            "s3_volatility":        float(s3),
            "s4_rsi_divergence":    float(s4),
            "s5_losing_streak":     float(s5),
            "s6_drawdown":          float(s6),
            "raw_irq_score":        float(raw_irq),
            "irq_score":            float(irq_final),
            "rsi":                  float(rsi),
            "volatility":           float(s3),
            "atr":                  float(atr),
            "stop_loss_pct":        float(stop_pct),
            "valid":                True,
        }

    @staticmethod
    def _logistic_transform(x: float, k: float = 5.0, theta: float = 0.5) -> float:
        """
        Transforma score em probabilidade usando função logística.
        P = 1 / (1 + e^(-k(x - theta)))

        Args:
            x: Valor de entrada
            k: Sensibilidade (padrão 5)
            theta: Ponto crítico (padrão 0.5)

        Returns:
            Valor entre 0 e 1
        """
        try:
            return 1.0 / (1.0 + math.exp(-k * (x - theta)))
        except OverflowError:
            return 0.0 if x < theta else 1.0

    @staticmethod
    def get_protection_level(irq_score: float) -> Dict[str, any]:
        """
        Determina o nível de proteção baseado no IRQ.

        Args:
            irq_score: Score de IRQ (0 a 1)

        Returns:
            Dict com nível de proteção e redução recomendada
        """
        if irq_score >= settings.IRQ_THRESHOLD_CRITICAL:
            return {
                "level": "CRÍTICO",
                "reduction_percentage": 1.0,  # 100% - sair totalmente
                "allow_new_positions": False,
                "color": "🔴",
            }
        elif irq_score >= settings.IRQ_THRESHOLD_VERY_HIGH:
            return {
                "level": "MUITO_ALTO",
                "reduction_percentage": settings.IRQ_REDUCTION_HIGH,
                "allow_new_positions": False,
                "color": "🟠",
            }
        elif irq_score >= settings.IRQ_THRESHOLD_HIGH:
            return {
                "level": "ALTO",
                "reduction_percentage": settings.IRQ_REDUCTION_MODERATE,
                "allow_new_positions": True,
                "color": "🟡",
            }
        else:
            return {
                "level": "NORMAL",
                "reduction_percentage": 0.0,
                "allow_new_positions": True,
                "color": "🟢",
            }
