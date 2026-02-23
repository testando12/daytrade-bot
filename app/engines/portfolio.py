"""
Engine de Alocação Dinâmica de Capital v2
Melhorias:
- Kelly Criterion fracionário para sizing de posição
- Concentração máxima por ativo e setor (B3 vs Crypto)
- Só aloca em ativos com entry_valid = True (sinal de qualidade)
- Tamanho da posição proporcional ao score E ao ATR (menos volátil = mais capital)
- Performance tracker integrado (win rate histórico)
"""

import math
from typing import Dict, List
from app.core.config import settings
from app.engines.momentum import MomentumAnalyzer
from app.engines.risk import RiskAnalyzer


class PortfolioManager:
    """Gerenciador de portfólio v2 com Kelly Criterion"""

    # Fração Kelly (0.25 = quarter Kelly — conservador e mais seguro)
    KELLY_FRACTION = 0.25

    # Concentração máxima por ativo
    MAX_SINGLE_ASSET_PCT = 0.35   # 35% por ativo
    MAX_CRYPTO_TOTAL_PCT = 0.50   # 50% total em crypto
    MAX_B3_TOTAL_PCT     = 0.60   # 60% total em B3

    # Win rate padrão inicial (calibrado pelos backtest históricos)
    # Assunção conservadora: 52% (ligeiramente acima do acaso)
    DEFAULT_WIN_RATE = 0.52
    DEFAULT_REWARD_RISK = 1.5   # ganho médio / perda média

    CRYPTO_SYMBOLS = {"BTC", "ETH", "BNB", "SOL", "ADA", "XRP", "DOGE"}

    @staticmethod
    def _kelly_size(
        win_rate: float,
        reward_risk_ratio: float,
        total_capital: float,
        kelly_fraction: float = 0.25,
    ) -> float:
        """
        Kelly Criterion fracionário.
        f* = (p * b - q) / b   onde b = reward/risk, p = win_rate, q = 1-p
        Usamos apenas uma fração (quarter Kelly) para reduzir drawdown.
        """
        b = max(reward_risk_ratio, 0.1)
        p = max(0.01, min(0.99, win_rate))
        q = 1.0 - p
        kelly_full = (p * b - q) / b
        kelly_full = max(0.0, kelly_full)  # nunca negativo
        return total_capital * kelly_full * kelly_fraction

    @staticmethod
    def calculate_portfolio_allocation(
        momentum_scores: Dict[str, float],
        irq_score: float,
        total_capital: float,
        momentum_details: Dict = None,
    ) -> Dict[str, float]:
        """
        Alocação baseada em Kelly + score de momentum.

        Só aloca em ativos com:
        1. momentum_score > ENTRY_THRESHOLD
        2. entry_valid = True (qualidade do sinal confirmada)
        3. Limites de concentração respeitados

        Args:
            momentum_scores: score por ativo
            irq_score: risco global (0=seguro, 1=crítico)
            total_capital: capital disponível
            momentum_details: dicionário completo com entry_valid, atr_pct etc.
        """
        risk_multiplier = max(0.0, 1.0 - irq_score * 1.5)  # IRQ penaliza mais agressivamente
        kelly_base = PortfolioManager._kelly_size(
            PortfolioManager.DEFAULT_WIN_RATE,
            PortfolioManager.DEFAULT_REWARD_RISK,
            total_capital,
            PortfolioManager.KELLY_FRACTION,
        )

        # Filtrar candidatos válidos
        candidates = {}
        for asset, score in momentum_scores.items():
            entry_valid = True
            atr_pct = 0.01
            if momentum_details and asset in momentum_details:
                entry_valid = momentum_details[asset].get("entry_valid", True)
                atr_pct = momentum_details[asset].get("atr_pct", 0.01)

            if score > MomentumAnalyzer.ENTRY_THRESHOLD and entry_valid:
                # Ajuste pelo ATR: mais volátil = menor posição
                atr_factor = 1.0 / (1.0 + atr_pct * 10)
                candidates[asset] = score * atr_factor

        if not candidates:
            # Sem sinais válidos: tudo em caixa
            return {asset: 0.0 for asset in momentum_scores}

        total_score = sum(candidates.values())

        # Mínimo de posição proporcional ao capital:
        # com pouco capital (ex R$150) o mínimo fixo (R$10) filtra tudo.
        # Usamos 1% do capital, mas nunca menos de R$1 nem mais que MIN_POSITION_AMOUNT.
        dynamic_min = max(1.0, min(settings.MIN_POSITION_AMOUNT, total_capital * 0.01))

        allocation = {}
        crypto_total = 0.0
        b3_total = 0.0

        # Ordenar por score (maior primeiro)
        for asset in sorted(candidates, key=candidates.get, reverse=True):
            score_weight = candidates[asset] / total_score
            raw_position = kelly_base * score_weight * risk_multiplier

            # Limite por ativo
            max_pos = total_capital * PortfolioManager.MAX_SINGLE_ASSET_PCT
            raw_position = min(raw_position, max_pos)

            # Limite por setor
            is_crypto = asset in PortfolioManager.CRYPTO_SYMBOLS
            if is_crypto:
                available = total_capital * PortfolioManager.MAX_CRYPTO_TOTAL_PCT - crypto_total
            else:
                available = total_capital * PortfolioManager.MAX_B3_TOTAL_PCT - b3_total

            raw_position = min(raw_position, max(0.0, available))

            if raw_position >= dynamic_min:
                allocation[asset] = raw_position
                if is_crypto:
                    crypto_total += raw_position
                else:
                    b3_total += raw_position
            else:
                allocation[asset] = 0.0

        # Ativos não candidatos ficam com 0
        for asset in momentum_scores:
            if asset not in allocation:
                allocation[asset] = 0.0

        return allocation

    @staticmethod
    def apply_rebalancing_rules(
        current_allocation: Dict[str, float],
        momentum_scores: Dict[str, Dict],
        total_capital: float,
        irq_score: float,
    ) -> Dict[str, Dict]:
        """
        Rebalanceamento com regras aprimoradas:
        - FORTE_ALTA + entry_valid: aumentar 25%
        - ALTA_LEVE: manter
        - LATERAL: reduzir metade
        - QUEDA: sair (zerar)
        - IRQ alto: proteger tudo
        """
        rebalancing = {}
        risk_protection = RiskAnalyzer.get_protection_level(irq_score)
        reduction = risk_protection["reduction_percentage"]

        for asset, score_data in momentum_scores.items():
            classification = score_data.get("classification", "LATERAL")
            entry_valid    = score_data.get("entry_valid", False)
            current_amount = current_allocation.get(asset, 0.0)
            protected_amount = current_amount * (1.0 - reduction)

            if classification == "FORTE_ALTA" and entry_valid:
                new_amount = protected_amount * 1.25
            elif classification == "FORTE_ALTA":
                new_amount = protected_amount  # score bom mas sinal fraco: manter
            elif classification == "ALTA_LEVE":
                new_amount = protected_amount
            elif classification == "LATERAL":
                new_amount = protected_amount * 0.5  # reduzir metade
            else:  # QUEDA
                new_amount = 0.0  # sair

            # Limite máximo
            max_pos = total_capital * PortfolioManager.MAX_SINGLE_ASSET_PCT
            new_amount = min(new_amount, max_pos)

            # Abaixo do mínimo dinâmico: zerar
            dynamic_min_rebal = max(1.0, min(settings.MIN_POSITION_AMOUNT, total_capital * 0.01))
            if new_amount < dynamic_min_rebal:
                new_amount = 0.0

            rebalancing[asset] = {
                "asset": asset,
                "classification": classification,
                "entry_valid": entry_valid,
                "current_amount": current_amount,
                "protected_amount": protected_amount,
                "recommended_amount": new_amount,
                "action": PortfolioManager._get_action(current_amount, new_amount),
                "change_percentage": (
                    ((new_amount - current_amount) / current_amount * 100)
                    if current_amount > 0
                    else (100.0 if new_amount > 0 else 0.0)
                ),
            }

        return rebalancing

    @staticmethod
    def _get_action(current: float, recommended: float) -> str:
        """Determina a ação a ser tomada"""
        threshold = 5.0  # R$ 5 de diferença

        if abs(current - recommended) < threshold:
            return "HOLD"
        elif recommended > current:
            return "BUY"
        elif recommended < current:
            return "SELL"
        else:
            return "HOLD"

    @staticmethod
    def calculate_risk_metrics(
        allocation: Dict[str, float], total_capital: float
    ) -> Dict[str, float]:
        """
        Calcula métricas de risco do portfólio.

        Args:
            allocation: Alocação por ativo
            total_capital: Capital total

        Returns:
            Dict com métricas de risco
        """
        total_allocated = sum(allocation.values())
        cash_available = max(0, total_capital - total_allocated)
        cash_percentage = (cash_available / total_capital * 100) if total_capital > 0 else 0

        # Concentração de risco
        all_positions = list(allocation.values())
        max_position = max(all_positions) if all_positions else 0
        max_position_pct = (max_position / total_capital * 100) if total_capital > 0 else 0

        # Número de posições ativas
        active_positions = sum(1 for v in allocation.values() if v > settings.MIN_POSITION_AMOUNT)

        return {
            "total_allocated": total_allocated,
            "cash_available": cash_available,
            "cash_percentage": cash_percentage,
            "max_position_percentage": max_position_pct,
            "active_positions": active_positions,
            "diversification_ratio": active_positions / len(allocation) if allocation else 0,
        }
