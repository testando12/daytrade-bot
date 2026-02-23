"""
Módulo de Predição com Machine Learning
Implementa regressão linear e ponderação de sinais para prever movimento de preço
"""

from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
import math


class SimpleLinearRegression:
    """Regressão Linear Simples (sem dependências externas)"""
    
    def __init__(self):
        self.slope = 0.0
        self.intercept = 0.0
        self.r_squared = 0.0
    
    def fit(self, x: List[float], y: List[float]) -> bool:
        """Treina o modelo com dados (x, y)"""
        if len(x) < 2 or len(x) != len(y):
            return False
        
        n = len(x)
        
        # Calcular médias
        x_mean = sum(x) / n
        y_mean = sum(y) / n
        
        # Calcular slope e intercept
        numerator = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
        
        if denominator == 0:
            return False
        
        self.slope = numerator / denominator
        self.intercept = y_mean - self.slope * x_mean
        
        # Calcular R²
        ss_res = sum((y[i] - (self.slope * x[i] + self.intercept)) ** 2 for i in range(n))
        ss_tot = sum((y[i] - y_mean) ** 2 for i in range(n))
        
        self.r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
        return True
    
    def predict(self, x: float) -> float:
        """Prediz y para um x dado"""
        return self.slope * x + self.intercept


class ExponentialSmoothing:
    """Suavização Exponencial para detectar tendência"""
    
    def __init__(self, alpha: float = 0.3):
        """alpha: fator de suavização (0-1). Maior = responde mais rápido"""
        self.alpha = alpha
        self.last_value = None
    
    def update(self, new_value: float) -> float:
        """Atualiza com novo valor e retorna suavizado"""
        if self.last_value is None:
            self.last_value = new_value
            return new_value
        
        smoothed = self.alpha * new_value + (1 - self.alpha) * self.last_value
        self.last_value = smoothed
        return smoothed
    
    def reset(self):
        self.last_value = None


class PricePredictorML:
    """Preditor de Preço usando ML"""
    
    def __init__(self, lookback_periods: int = 20):
        self.lookback_periods = lookback_periods
        self.models: Dict[str, SimpleLinearRegression] = {}
        self.smoothers: Dict[str, ExponentialSmoothing] = {}
        self.price_history: Dict[str, List[float]] = {}
        self.volume_history: Dict[str, List[float]] = {}
    
    def add_price_data(self, asset: str, prices: List[float], volumes: Optional[List[float]] = None):
        """Adiciona dados de treino"""
        if len(prices) < self.lookback_periods:
            return False
        
        self.price_history[asset] = prices[-self.lookback_periods:]
        if volumes:
            self.volume_history[asset] = volumes[-self.lookback_periods:]
        else:
            self.volume_history[asset] = [1.0] * len(self.price_history[asset])
        
        # Treinar regressão linear
        x = list(range(len(self.price_history[asset])))
        y = self.price_history[asset]
        
        model = SimpleLinearRegression()
        if model.fit(x, y):
            self.models[asset] = model
            return True
        return False
    
    def predict_next_price(self, asset: str, periods_ahead: int = 5) -> Optional[Dict]:
        """Prediz próximo preço"""
        
        if asset not in self.models or asset not in self.price_history:
            return None
        
        model = self.models[asset]
        current_x = len(self.price_history[asset]) - 1
        next_x = current_x + periods_ahead
        
        current_price = self.price_history[asset][-1]
        predicted_price = model.predict(float(next_x))
        
        price_change = predicted_price - current_price
        price_change_pct = (price_change / current_price * 100) if current_price > 0 else 0
        
        confidence = (
            (abs(model.r_squared) * 100) +  # Quão bem o modelo se ajusta
            (min(len(self.price_history[asset]) / 50, 1) * 100) / 2  # Quantidade de dados
        ) / 1.5
        confidence = min(max(confidence, 0), 100)  # Limpar entre 0-100
        
        direction = "UP" if price_change > 0 else "DOWN" if price_change < 0 else "FLAT"
        
        return {
            "asset": asset,
            "current_price": current_price,
            "predicted_price": predicted_price,
            "price_change": price_change,
            "price_change_pct": price_change_pct,
            "direction": direction,
            "confidence": confidence,
            "periods_ahead": periods_ahead,
            "model_fit_r_squared": model.r_squared
        }
    
    def get_trend_strength(self, asset: str) -> Optional[Dict]:
        """Mede força da tendência"""
        
        if asset not in self.models or asset not in self.price_history:
            return None
        
        model = self.models[asset]
        prices = self.price_history[asset]
        
        # Calcular mudança acumulada
        total_change = prices[-1] - prices[0]
        total_change_pct = (total_change / prices[0] * 100) if prices[0] > 0 else 0
        
        # Volatilidade (desvio padrão)
        mean_price = sum(prices) / len(prices)
        variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
        volatility = math.sqrt(variance)
        volatility_pct = (volatility / mean_price * 100) if mean_price > 0 else 0
        
        # Força da tendência (slope magnitude)
        trend_strength = abs(model.slope) / (mean_price / len(prices)) if mean_price > 0 else 0
        
        # Consistência (% de subidas/descidas)
        ups = sum(1 for i in range(1, len(prices)) if prices[i] > prices[i-1])
        consistency = (ups / (len(prices) - 1) * 100) if len(prices) > 1 else 50
        
        return {
            "asset": asset,
            "total_change_pct": total_change_pct,
            "volatility_pct": volatility_pct,
            "trend_strength": trend_strength,
            "consistency_pct": consistency,
            "slope": model.slope,
            "average_price": mean_price
        }
    
    def predict_support_resistance(self, asset: str) -> Optional[Dict]:
        """Prediz níveis de suporte e resistência"""
        
        if asset not in self.price_history:
            return None
        
        prices = self.price_history[asset]
        current_price = prices[-1]
        
        # Máximos e mínimos
        highest = max(prices)
        lowest = min(prices)
        price_range = highest - lowest
        
        # Suporte: 70% do range abaixo do mínimo recente
        support = lowest - (price_range * 0.3)
        
        # Resistência: 70% do range acima do máximo recente
        resistance = highest + (price_range * 0.3)
        
        # Pivô
        pivot = (highest + lowest + current_price) / 3
        
        return {
            "asset": asset,
            "current_price": current_price,
            "support": support,
            "pivot": pivot,
            "resistance": resistance,
            "range": price_range,
            "nearest_support_distance": current_price - support,
            "nearest_resistance_distance": resistance - current_price
        }
    
    def calculate_ml_signal(self, asset: str) -> Optional[Dict]:
        """Calcula sinal combinado de ML"""
        
        prediction = self.predict_next_price(asset, periods_ahead=5)
        trend = self.get_trend_strength(asset)
        support_res = self.predict_support_resistance(asset)
        
        if not all([prediction, trend, support_res]):
            return None
        
        # Scoring de -100 (VENDER) a +100 (COMPRAR)
        score = 0
        
        # Fator 1: Direção prevista (±20 pontos)
        if prediction["direction"] == "UP":
            score += 20 * (prediction["confidence"] / 100)
        elif prediction["direction"] == "DOWN":
            score -= 20 * (prediction["confidence"] / 100)
        
        # Fator 2: Força da tendência (±25 pontos)
        trend_factor = min(trend["trend_strength"] * 10, 25)
        if prediction["direction"] == "UP":
            score += trend_factor
        elif prediction["direction"] == "DOWN":
            score -= trend_factor
        
        # Fator 3: Volatilidade (penaliza alta volatilidade)
        volatility_penalty = -min(trend["volatility_pct"] / 4, 20)
        score += volatility_penalty
        
        # Fator 4: Distância de suporte/resistência
        if support_res["nearest_support_distance"] < support_res["nearest_resistance_distance"]:
            # Perto do suporte = mais downside = vender
            score -= 15
        else:
            # Perto da resistência = menos upside = vender
            score -= 10
        
        # Fator 5: Consistência da tendência (±20 pontos)
        consistency_bonus = (trend["consistency_pct"] - 50) / 2.5
        if prediction["direction"] == "UP":
            score += consistency_bonus
        else:
            score -= consistency_bonus
        
        # Limitar score entre -100 e 100
        score = max(min(score, 100), -100)
        
        # Determinar ação
        if score > 50:
            action = "STRONG_BUY"
        elif score > 20:
            action = "BUY"
        elif score > -20:
            action = "HOLD"
        elif score > -50:
            action = "SELL"
        else:
            action = "STRONG_SELL"
        
        return {
            "asset": asset,
            "ml_score": score,
            "action": action,
            "confidence": prediction["confidence"],
            "prediction": prediction,
            "trend": trend,
            "support_resistance": support_res,
            "factors": {
                "direction": 20 if prediction["direction"] == "UP" else -20 if prediction["direction"] == "DOWN" else 0,
                "trend_strength": trend_factor,
                "volatility": volatility_penalty,
                "support_resistance": -15 if support_res["nearest_support_distance"] < support_res["nearest_resistance_distance"] else -10,
                "consistency": consistency_bonus
            }
        }


# ML Ensemble - combina múltiplas estratégias
class MLEnsemble:
    """Combina múltiplas técnicas de ML para decisão mais robusta"""
    
    def __init__(self):
        self.predictor = PricePredictorML(lookback_periods=20)
    
    def train(self, market_data: Dict[str, Dict]) -> bool:
        """Treina com dados de mercado"""
        for asset, data in market_data.items():
            if "prices" in data:
                prices = data["prices"]
                volumes = data.get("volumes", None)
                self.predictor.add_price_data(asset, prices, volumes)
        return True
    
    def predict_all(self, assets: List[str]) -> List[Dict]:
        """Prediz para múltiplos ativos"""
        predictions = []
        for asset in assets:
            signal = self.predictor.calculate_ml_signal(asset)
            if signal:
                predictions.append(signal)
        return predictions
    
    def get_recommendation(self, ml_signal: Dict, momentum_score: float, irq_score: float) -> Dict:
        """Combina ML signal com momentum e risco"""
        
        ml_action = ml_signal["action"]
        ml_score = ml_signal["ml_score"]
        
        # Ajustar pela análise de risco
        risk_multiplier = 1 - (irq_score * 0.5)  # Alto risco = ação mais conservadora
        
        # Ajustar pela análise de momentum
        momentum_factor = momentum_score * 30  # -30 a +30 pontos
        
        # Score final combinado
        final_score = (ml_score * 0.6 + momentum_factor * 0.4) * risk_multiplier
        
        # Determinar recomendação final
        if final_score > 50:
            recommendation = "STRONG_BUY"
        elif final_score > 20:
            recommendation = "BUY"
        elif final_score > -20:
            recommendation = "HOLD"
        elif final_score > -50:
            recommendation = "SELL"
        else:
            recommendation = "STRONG_SELL"
        
        return {
            "asset": ml_signal["asset"],
            "ml_action": ml_action,
            "ml_score": ml_score,
            "momentum_factor": momentum_factor,
            "risk_multiplier": risk_multiplier,
            "final_score": final_score,
            "final_recommendation": recommendation,
            "confidence": ml_signal["confidence"],
            "rationale": f"ML: {ml_action} ({ml_score:.0f}) + Momentum ({momentum_factor:.0f}) - Risk (×{risk_multiplier:.2f})"
        }


if __name__ == "__main__":
    print("Módulo ML carregado!")
    print("Use: from ml_predictor import PricePredictorML, MLEnsemble")
