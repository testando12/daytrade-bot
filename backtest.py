"""
Script de Backtesting — simula performance do bot com dados históricos reais do Yahoo Finance.

Walk-forward: a cada passo, o model vê apenas os candles ANTERIORES ao ponto atual.
Sem look-ahead bias.
"""

import asyncio
import json
import statistics
from datetime import datetime, timedelta
from typing import Optional

from app.engines import MomentumAnalyzer, RiskAnalyzer, PortfolioManager
from app.core.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sharpe(returns: list, periods_per_year: int = 252) -> float:
    """Annualised Sharpe ratio (rf = 0)."""
    if len(returns) < 2:
        return 0.0
    mu = statistics.mean(returns)
    sigma = statistics.stdev(returns)
    if sigma == 0:
        return 0.0
    return (mu / sigma) * (periods_per_year ** 0.5)


def _max_drawdown(equity_curve: list) -> float:
    """Maximum drawdown as a percentage of peak equity (negative number)."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        dd = (v - peak) / peak * 100
        max_dd = min(max_dd, dd)
    return max_dd


class BacktestEngine:
    """
    Engine para backtesting walk-forward de estratégias.

    Supports two data modes:
    - ``data`` dict already provided (offline / test)
    - ``fetch=True`` → downloads real historical klines via Yahoo Finance
    """

    MIN_BARS = 30        # mínimo de candles antes de iniciar simulação

    def __init__(self, initial_capital: float, data: Optional[dict] = None):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        # data: {asset: {"prices": [...], "volumes": [...]}}
        self.data = data or {}
        self.history: list = []
        self.trades: list = []
        self.portfolio_value_history: list = [initial_capital]
        self.daily_returns: list = []        # per-rebalance return fractions

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_real_data(self, assets: list, interval: str = "1d", limit: int = 120) -> bool:
        """
        Baixa dados históricos reais do Yahoo Finance via market_data_service.
        Retorna True se pelo menos um ativo foi obtido com sucesso.
        """
        try:
            from app.market_data import market_data_service, MARKET_DATA_AVAILABLE
            if not MARKET_DATA_AVAILABLE:
                print("[backtest] market_data_service indisponível — usando dados de teste")
                return False
            print(f"[backtest] Baixando {limit} candles ({interval}) para {len(assets)} ativos…")
            klines = await market_data_service.get_all_klines(assets, interval, limit)
            if klines:
                self.data = klines
                print(f"[backtest] Dados obtidos: {list(klines.keys())}")
                return True
        except Exception as e:
            print(f"[backtest] Erro ao buscar dados reais: {e}")
        return False

    def run_backtest(self, rebalance_interval: int = 5) -> dict:
        """
        Executa backtesting walk-forward.

        A cada passo o modelo recebe apenas os candles disponíveis até aquele
        momento (sem look-ahead bias). Os candles seguintes são usados como
        preço de saída para calcular o P&L.

        Args:
            rebalance_interval: número de candles entre rebalanceamentos.
        """
        if not self.data:
            return {"error": "Sem dados. Forneça data= ou chame await fetch_real_data() antes."}

        # alinha todos os assets para o menor número de candles
        min_len = min(len(v["prices"]) for v in self.data.values())
        if min_len < self.MIN_BARS + rebalance_interval:
            return {"error": f"Dados insuficientes ({min_len} candles). Mínimo: {self.MIN_BARS + rebalance_interval}"}

        wins = 0
        losses = 0

        for t in range(self.MIN_BARS, min_len - rebalance_interval, rebalance_interval):
            # ─── dados visíveis até o candle t (inclusive) ──────────────────
            snapshot = {
                asset: {
                    "prices":  v["prices"][:t],
                    "volumes": v["volumes"][:t],
                }
                for asset, v in self.data.items()
            }

            # ─── preços de saída = candle t + rebalance_interval ────────────
            exit_idx = min(t + rebalance_interval, min_len - 1)
            exit_prices = {asset: v["prices"][exit_idx] for asset, v in self.data.items()}

            # ─── análise ─────────────────────────────────────────────────────
            step = self._run_step(snapshot, exit_prices)
            if step["pnl"] > 0:
                wins += 1
            elif step["pnl"] < 0:
                losses += 1

            self.history.append(step)

        return self._generate_report(wins, losses)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run_step(self, snapshot: dict, exit_prices: dict) -> dict:
        """Roda um passo de análise + simulação e retorna métricas do passo."""
        # Momentum
        try:
            momentum_results = MomentumAnalyzer.calculate_multiple_assets(snapshot)
        except Exception:
            momentum_results = {}

        momentum_scores = {a: d["momentum_score"] for a, d in momentum_results.items()}

        # Risco (usar BTC ou primeiro ativo disponível)
        ref = "BTC" if "BTC" in snapshot else list(snapshot.keys())[0]
        ref_data = snapshot[ref]
        try:
            risk_analysis = RiskAnalyzer.calculate_irq(ref_data["prices"], ref_data["volumes"])
            irq_score = risk_analysis["irq_score"]
        except Exception:
            irq_score = 0.5

        # Alocação — para backtest usa-se a alocação pura (sem rebalanceamento de posições
        # existentes, pois no backtest assume-se portfólio zerado a cada período)
        try:
            allocation = PortfolioManager.calculate_portfolio_allocation(
                momentum_scores, irq_score, self.current_capital,
                momentum_details=momentum_results,
            )
        except Exception:
            allocation = {}

        # P&L: quanto ganhou/perdeu entre entry (último preço do snapshot)
        #       e saída (próximos candles)
        step_pnl = 0.0
        for asset, invested in allocation.items():
            if invested <= 0:
                continue
            entry = snapshot[asset]["prices"][-1] if asset in snapshot else None
            exit_ = exit_prices.get(asset)
            if entry and exit_ and entry > 0:
                ret = (exit_ - entry) / entry
                step_pnl += ret * invested

        prev_capital = self.current_capital
        self.current_capital += step_pnl
        self.portfolio_value_history.append(self.current_capital)

        if len(self.portfolio_value_history) > 1:
            period_ret = (self.current_capital - prev_capital) / prev_capital
            self.daily_returns.append(period_ret)

        return {
            "t":            len(self.history),
            "pnl":          round(step_pnl, 4),
            "irq_score":    round(irq_score, 4),
            "capital":      round(self.current_capital, 2),
            "active":       sum(1 for v in allocation.values() if v > 0),
        }

    def _generate_report(self, wins: int, losses: int) -> dict:
        """Agrega métricas finais do backtest."""
        if not self.history:
            return {"error": "Sem histórico de backtesting"}

        total_return_pct = (self.current_capital - self.initial_capital) / self.initial_capital * 100
        n_periods        = len(self.history)
        total_pnl        = self.current_capital - self.initial_capital

        # Win rate
        total_trades = wins + losses
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0.0

        # Drawdown máximo
        max_dd = _max_drawdown(self.portfolio_value_history)

        # Sharpe (anualizado assumindo rebalance diário ≈ 252 dias/ano)
        sharpe = _sharpe(self.daily_returns, periods_per_year=252)

        # P&L médio por período
        avg_pnl_period = total_pnl / n_periods if n_periods > 0 else 0.0

        # Estimar P&L médio diário (período = "1d" normalmente)
        avg_daily_pnl = avg_pnl_period  # se interval="1d"

        return {
            "initial_capital":    self.initial_capital,
            "final_capital":      round(self.current_capital, 2),
            "total_return_pct":   round(total_return_pct, 4),
            "total_pnl":          round(total_pnl, 2),
            "avg_daily_pnl":      round(avg_daily_pnl, 2),
            "max_portfolio_value": round(max(self.portfolio_value_history), 2),
            "min_portfolio_value": round(min(self.portfolio_value_history), 2),
            "max_drawdown_pct":   round(max_dd, 4),
            "sharpe_ratio":       round(sharpe, 4),
            "win_rate_pct":       round(win_rate, 2),
            "total_wins":         wins,
            "total_losses":       losses,
            "total_periods":      n_periods,
            "equity_curve":       [round(v, 2) for v in self.portfolio_value_history],
            "history":            self.history,
        }


# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------

async def run_real_backtest(
    assets: Optional[list] = None,
    interval: str = "1d",
    limit: int = 120,
    rebalance_interval: int = 1,
    initial_capital: Optional[float] = None,
) -> dict:
    """
    Ponte assíncrona para rodar backtest completo com dados reais do Yahoo Finance.
    Pode ser chamada diretamente de endpoints FastAPI.

    Args:
        assets: lista de ativos (default: settings.ALL_ASSETS)
        interval: "1d", "1h", "30m" etc.
        limit: quantos candles históricos buscar (máx 200 para Yahoo Finance gratuito)
        rebalance_interval: passos entre rebalanceamentos
        initial_capital: capital inicial (default: settings.INITIAL_CAPITAL)
    """
    if assets is None:
        assets = list(settings.ALL_ASSETS)
    if initial_capital is None:
        initial_capital = settings.INITIAL_CAPITAL

    engine = BacktestEngine(initial_capital=initial_capital)
    fetched = await engine.fetch_real_data(assets, interval=interval, limit=limit)

    if not fetched:
        # fallback: dados sintéticos mínimos para CI / demonstração offline
        print("[backtest] Usando dados sintéticos de fallback")
        import random, math
        rng = random.Random(42)
        def _fake(start, n):
            prices, vols = [start], [100]
            for _ in range(n - 1):
                prices.append(prices[-1] * (1 + rng.gauss(0.001, 0.015)))
                vols.append(abs(rng.gauss(100, 30)))
            return prices, vols
        for sym, base in [("BTC", 45000), ("ETH", 2500), ("PETR4", 38), ("VALE3", 87)]:
            p, v = _fake(base, limit)
            engine.data[sym] = {"prices": p, "volumes": v}

    report = engine.run_backtest(rebalance_interval=rebalance_interval)
    report["data_source"] = "yahoo.finance" if fetched else "synthetic"
    report["assets"]      = assets
    report["interval"]    = interval
    report["timestamp"]   = datetime.now().isoformat()
    return report


def run_example_backtest():
    """Executa backtest síncrono de exemplo (compatibilidade com scripts antigos)."""
    report = asyncio.run(run_real_backtest(
        assets=["BTC", "ETH", "PETR4", "VALE3"],
        interval="1d",
        limit=90,
        rebalance_interval=1,
    ))

    print("\n" + "=" * 60)
    print("RELATÓRIO DE BACKTESTING — DAY TRADE BOT")
    print("=" * 60)
    print(f"Capital Inicial:    R$ {report['initial_capital']:.2f}")
    print(f"Capital Final:      R$ {report['final_capital']:.2f}")
    print(f"Retorno Total:      {report['total_return_pct']:.2f}%")
    print(f"P&L Médio Diário:   R$ {report['avg_daily_pnl']:.2f}")
    print(f"Drawdown Máximo:    {report['max_drawdown_pct']:.2f}%")
    print(f"Sharpe Ratio:       {report['sharpe_ratio']:.4f}")
    print(f"Win Rate:           {report['win_rate_pct']:.1f}%")
    print(f"Períodos Testados:  {report['total_periods']}")
    print(f"Fonte de dados:     {report['data_source']}")
    print("=" * 60 + "\n")

    with open("backtest_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print("Relatório salvo em: backtest_report.json")
    return report


if __name__ == "__main__":
    run_example_backtest()
