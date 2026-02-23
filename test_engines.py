"""
Script para testar os engines do bot sem usar a API
Útil para debug e compreensão do funcionamento
"""

from app.engines import MomentumAnalyzer, RiskAnalyzer, PortfolioManager
from app.core.config import settings
import json


def print_section(title):
    """Printa uma seção formatada"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def test_momentum():
    """Testa análise de momentum"""
    print_section("1. TESTE DE MOMENTUM SCORE")

    test_data = {
        "BTC": {
            "prices": [42000, 42100, 42300, 42200, 42400, 42350, 42500, 42600, 42550, 42700, 42800, 42750, 42900, 43000, 43100, 43200, 43150, 43300, 43400, 43500, 43600, 43700],
            "volumes": [100, 110, 120, 105, 115, 108, 125, 130, 118, 128, 135, 122, 132, 140, 145, 150, 138, 148, 155, 160, 158, 165],
        },
        "ETH": {
            "prices": [2200, 2220, 2240, 2230, 2250, 2260, 2255, 2270, 2280, 2290, 2300, 2310, 2305, 2320, 2330, 2340, 2350, 2360, 2370, 2375, 2380, 2395],
            "volumes": [500, 510, 520, 505, 515, 525, 518, 530, 540, 535, 545, 550, 542, 555, 560, 565, 570, 575, 580, 578, 585, 590],
        },
    }

    results = MomentumAnalyzer.calculate_multiple_assets(test_data)

    for asset, data in results.items():
        print(f"\n📊 {asset}:")
        print(f"   Momentum Score: {data['momentum_score']:.4f}")
        print(f"   Classificação: {data['classification']}")
        print(f"   Retorno: {data['return_pct']:.2f}%")
        print(f"   Trend: {data['trend_status']}")
        print(f"   MA Curta: {data['ma_short']:.2f}")
        print(f"   MA Longa: {data['ma_long']:.2f}")


def test_risk():
    """Testa análise de risco"""
    print_section("2. TESTE DE RISCO (IRQ)")

    prices = [42000, 42100, 42300, 42200, 42400, 42350, 42500, 42600, 42550, 42700, 42800, 42750, 42900, 43000, 43100, 43200, 43150, 43300, 43400, 43500, 43600, 43700]
    volumes = [100, 110, 120, 105, 115, 108, 125, 130, 118, 128, 135, 122, 132, 140, 145, 150, 138, 148, 155, 160, 158, 165]

    risk_result = RiskAnalyzer.calculate_irq(prices, volumes)

    print(f"\n🔴 IRQ Score: {risk_result['irq_score']:.4f}")
    print(f"   RSI: {risk_result['rsi']:.2f}")
    print(f"   Volatilidade: {risk_result['volatility']:.4f}")
    print(f"\n   Sinais de Risco:")
    print(f"   S1 (Perda de Tendência): {risk_result['s1_trend_loss']:.4f}")
    print(f"   S2 (Pressão Vendedora): {risk_result['s2_selling_pressure']:.4f}")
    print(f"   S3 (Volatilidade): {risk_result['s3_volatility']:.4f}")
    print(f"   S4 (Divergência RSI): {risk_result['s4_rsi_divergence']:.4f}")
    print(f"   S5 (Sequência de Quedas): {risk_result['s5_losing_streak']:.4f}")

    protection = RiskAnalyzer.get_protection_level(risk_result['irq_score'])
    print(f"\n   Nível de Proteção: {protection['level']} {protection['color']}")
    print(f"   Redução Recomendada: {protection['reduction_percentage']*100:.0f}%")


def test_portfolio():
    """Testa alocação de portfólio"""
    print_section("3. TESTE DE ALOCAÇÃO DE PORTFÓLIO")

    # Momentum scores de test
    momentum_scores = {
        "BTC": 0.45,
        "ETH": -0.15,
        "BNB": 0.25,
        "SOL": 0.30,
        "ADA": -0.20,
    }

    # IRQ score (risco baixo)
    irq_score = 0.35

    # Capital inicial
    initial_capital = settings.INITIAL_CAPITAL

    # Calcular alocação
    allocation = PortfolioManager.calculate_portfolio_allocation(
        momentum_scores,
        irq_score,
        initial_capital,
    )

    print(f"\n💰 Capital Inicial: R$ {initial_capital:.2f}")
    print(f"🔴 IRQ Score: {irq_score:.2f}")
    print(f"\n📊 Alocação Recomendada:")

    total_allocated = 0
    for asset, amount in allocation.items():
        if amount > 0:
            pct = (amount / initial_capital) * 100
            print(f"   {asset}: R$ {amount:.2f} ({pct:.1f}%)")
            total_allocated += amount

    cash = initial_capital - total_allocated
    print(f"\n   Caixa: R$ {cash:.2f} ({(cash/initial_capital)*100:.1f}%)")


def test_rebalance():
    """Testa rebalanceamento automático"""
    print_section("4. TESTE DE REBALANCEAMENTO")

    # Dados de momentum
    momentum_scores = {
        "BTC": {"momentum_score": 0.55, "classification": "FORTE_ALTA"},
        "ETH": {"momentum_score": 0.05, "classification": "ALTA_LEVE"},
        "BNB": {"momentum_score": -0.10, "classification": "LATERAL"},
        "SOL": {"momentum_score": -0.35, "classification": "QUEDA"},
        "ADA": {"momentum_score": -0.20, "classification": "QUEDA"},
    }

    # Alocação atual
    current_allocation = {
        "BTC": 50.0,
        "ETH": 40.0,
        "BNB": 20.0,
        "SOL": 30.0,
        "ADA": 0.0,
    }

    irq_score = 0.35
    initial_capital = settings.INITIAL_CAPITAL

    # Rebalancear
    rebalance_results = PortfolioManager.apply_rebalancing_rules(
        current_allocation,
        momentum_scores,
        initial_capital,
        irq_score,
    )

    print(f"\n📊 Rebalanceamento Automático:")
    print(f"   IRQ Level: Normal (sem proteção)\n")

    for asset, rebal in rebalance_results.items():
        if rebal["current_amount"] > 0 or rebal["recommended_amount"] > 0:
            print(f"   {asset}:")
            print(f"      Antes: R$ {rebal['current_amount']:.2f}")
            print(f"      Depois: R$ {rebal['recommended_amount']:.2f}")
            print(f"      Ação: {rebal['action']}")
            if rebal['action'] != "HOLD":
                print(f"      Mudança: {rebal['change_percentage']:.1f}%")


def test_risk_protection():
    """Testa proteção de risco"""
    print_section("5. TESTE DE PROTEÇÃO DE RISCO")

    test_scenarios = [
        ("Normal", 0.35),
        ("Alto Risco", 0.72),
        ("Muito Alto Risco", 0.82),
        ("Crítico", 0.92),
    ]

    for scenario_name, irq_score in test_scenarios:
        protection = RiskAnalyzer.get_protection_level(irq_score)
        print(f"\n{scenario_name} (IRQ: {irq_score:.0%}):")
        print(f"   Nível: {protection['level']} {protection['color']}")
        print(f"   Redução: {protection['reduction_percentage']*100:.0f}%")
        print(f"   Novas Posições: {'Sim' if protection['allow_new_positions'] else 'Não'}")


def main():
    """Executa todos os testes"""
    print("\n" + "🤖 " * 15)
    print("   DAY TRADE BOT - TESTE DE ENGINES")
    print("🤖 " * 15)

    try:
        test_momentum()
        test_risk()
        test_portfolio()
        test_rebalance()
        test_risk_protection()

        print_section("✅ TODOS OS TESTES CONCLUÍDOS COM SUCESSO")

    except Exception as e:
        print(f"\n❌ ERRO: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
