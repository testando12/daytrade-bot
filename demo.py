"""
Demonstração simples do Day Trade Bot
Roda uma simulação completa sem dependência de banco de dados ou FastAPI
"""

import time
from datetime import datetime, timedelta
from app.engines import MomentumAnalyzer, RiskAnalyzer, PortfolioManager
from app.core.config import settings


def print_header(iteration):
    """Printa cabeçalho da iteração"""
    print("\n" + "=" * 80)
    print(f"  ITERAÇÃO #{iteration} - {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 80)


def print_momentum(results):
    """Mostra análise de momentum"""
    print("\n📊 ANÁLISE DE MOMENTUM")
    print("-" * 80)
    for asset, data in sorted(results.items()):
        classification = data["classification"]
        emoji = {
            "FORTE_ALTA": "🔥",
            "ALTA_LEVE": "📈",
            "LATERAL": "⚖️",
            "QUEDA": "📉",
        }.get(classification, "❓")

        print(
            f"  {emoji} {asset:5} | Score: {data['momentum_score']:7.4f} | "
            f"Trend: {data['trend_status']:6} | Return: {data['return_pct']:7.2f}%"
        )


def print_risk(risk_result):
    """Mostra análise de risco"""
    print("\n🔴 ANÁLISE DE RISCO (IRQ)")
    print("-" * 80)
    irq_score = risk_result["irq_score"]
    protection = RiskAnalyzer.get_protection_level(irq_score)

    print(f"  IRQ Score: {irq_score:.1%} | Nível: {protection['level']} {protection['color']}")
    print(f"  RSI: {risk_result['rsi']:.1f} | Volatilidade: {risk_result['volatility']:.1%}")
    print(f"\n  Sinais de Risco:")
    print(f"    S1 (Tendência): {risk_result['s1_trend_loss']:.2f}")
    print(f"    S2 (Pressão):   {risk_result['s2_selling_pressure']:.2f}")
    print(f"    S3 (Volat.):    {risk_result['s3_volatility']:.2f}")
    print(f"    S4 (RSI):       {risk_result['s4_rsi_divergence']:.2f}")
    print(f"    S5 (Quedas):    {risk_result['s5_losing_streak']:.2f}")


def print_allocation(rebalancing, capital, risk_metrics):
    """Mostra alocação recomendada"""
    print("\n💰 ALOCAÇÃO DE CAPITAL")
    print("-" * 80)
    print(f"  Capital Total: R$ {capital:.2f}")
    print(f"  Cash Disponível: R$ {risk_metrics['cash_available']:.2f}")
    print(f"  Posições Ativas: {risk_metrics['active_positions']}")
    print()

    for asset, rebal in sorted(rebalancing.items()):
        if rebal["recommended_amount"] > 0:
            action_emoji = {"BUY": "📈", "SELL": "📉", "HOLD": "⚖️"}.get(rebal["action"], "❓")
            print(
                f"  {action_emoji} {asset:5} | {rebal['recommended_amount']:7.2f} "
                f"| {rebal['action']:4} | {rebal['change_percentage']:+7.1f}%"
            )


def simulate_bot(iterations=3, delay=2):
    """Simula o bot rodando"""
    print("\n" + "🤖 " * 25)
    print("     DAY TRADE BOT - SIMULAÇÃO DE FUNCIONAMENTO")
    print("🤖 " * 25)

    # Dados de teste (vamos variar um pouco a cada iteração)
    # 22 pontos para cobrir period_long=20 + margem
    base_data = {
        "BTC": {
            "prices": [
                41000, 41500, 41800, 42000, 41700, 42200, 42500, 42300, 42800, 43000,
                42900, 43100, 43200, 43000, 42800, 42900, 43300, 43400, 43500, 43600, 43700, 43900,
            ],
            "volumes": [
                80, 90, 95, 100, 88, 110, 120, 108, 115, 100,
                92, 105, 120, 110, 90, 95, 130, 140, 125, 115, 120, 135,
            ],
        },
        "ETH": {
            "prices": [
                2150, 2180, 2200, 2220, 2190, 2210, 2240, 2225, 2260, 2290,
                2280, 2300, 2320, 2310, 2300, 2290, 2310, 2330, 2340, 2350, 2360, 2375,
            ],
            "volumes": [
                420, 440, 460, 480, 450, 490, 510, 495, 505, 500,
                488, 505, 520, 510, 480, 490, 530, 550, 540, 530, 540, 560,
            ],
        },
        "BNB": {
            "prices": [
                570, 575, 580, 585, 578, 582, 590, 587, 595, 600,
                598, 602, 605, 602, 598, 597, 605, 610, 612, 615, 618, 622,
            ],
            "volumes": [
                170, 175, 180, 190, 178, 185, 195, 188, 198, 200,
                192, 200, 210, 205, 190, 195, 220, 230, 225, 220, 225, 235,
            ],
        },
        "SOL": {
            "prices": [
                160, 163, 166, 169, 165, 168, 172, 170, 175, 178,
                177, 179, 182, 181, 179, 178, 182, 185, 186, 188, 190, 193,
            ],
            "volumes": [
                250, 260, 265, 275, 262, 270, 285, 278, 290, 300,
                292, 300, 310, 305, 280, 290, 320, 340, 330, 320, 330, 345,
            ],
        },
    }

    portfolio_history = []
    current_portfolio = {asset: 0.0 for asset in base_data.keys()}
    current_capital = settings.INITIAL_CAPITAL

    for iteration in range(1, iterations + 1):
        print_header(iteration)

        # Simular pequenas mudanças nos preços
        current_data = {}
        for asset, data in base_data.items():
            # Adicionar novo ponto de preço
            prices = data["prices"] + [data["prices"][-1] * (1 + (iteration * 0.001 - 0.002))]
            volumes = data["volumes"] + [data["volumes"][-1] * (1 + (iteration * 0.01))]
            current_data[asset] = {"prices": prices[-25:], "volumes": volumes[-25:]}  # Manter últimos 25 (> period_long=20)

        # 1. Análise de Momentum
        momentum_results = MomentumAnalyzer.calculate_multiple_assets(current_data)
        print_momentum(momentum_results)

        # 2. Análise de Risco
        btc_data = current_data.get("BTC", {})
        risk_result = RiskAnalyzer.calculate_irq(btc_data.get("prices", []), btc_data.get("volumes", []))
        print_risk(risk_result)

        # 3. Alocação
        momentum_scores = {asset: data["momentum_score"] for asset, data in momentum_results.items()}
        allocation = PortfolioManager.calculate_portfolio_allocation(
            momentum_scores, risk_result["irq_score"], current_capital
        )

        # 4. Rebalanceamento
        rebalancing = PortfolioManager.apply_rebalancing_rules(
            current_portfolio, momentum_results, current_capital, risk_result["irq_score"]
        )

        # 5. Métricas
        risk_metrics = PortfolioManager.calculate_risk_metrics(allocation, current_capital)

        print_allocation(rebalancing, current_capital, risk_metrics)

        # Simular P&L
        portfolio_pnl = sum(
            (current_data[asset]["prices"][-1] - current_data[asset]["prices"][-2])
            / current_data[asset]["prices"][-2]
            * current_portfolio[asset]
            for asset in current_portfolio.keys()
            if current_data[asset]["prices"][-2] != 0
        )

        current_capital += portfolio_pnl

        portfolio_info = {
            "iteration": iteration,
            "capital": current_capital,
            "pnl": portfolio_pnl,
            "irq": risk_result["irq_score"],
        }
        portfolio_history.append(portfolio_info)

        # Atualizar portfólio
        current_portfolio = {asset: rebal["recommended_amount"] for asset, rebal in rebalancing.items()}

        # Aguardar antes da próxima iteração
        if iteration < iterations:
            print(f"\n⏳ Próxima iteração em {delay}s...")
            time.sleep(delay)

    # Resumo final
    print("\n" + "=" * 80)
    print("  RESUMO DA SIMULAÇÃO")
    print("=" * 80)
    print(f"Capital Inicial: R$ {settings.INITIAL_CAPITAL:.2f}")
    print(f"Capital Final: R$ {current_capital:.2f}")
    print(f"Retorno: {((current_capital - settings.INITIAL_CAPITAL) / settings.INITIAL_CAPITAL * 100):.2f}%")
    print(f"Iterações: {iterations}")
    print("=" * 80 + "\n")


def main():
    """Função principal"""
    try:
        simulate_bot(iterations=3, delay=1)
        print("✅ Simulação concluída com sucesso!")

    except Exception as e:
        print(f"\n❌ Erro: {str(e)}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
