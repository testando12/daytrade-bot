"""
Projeção Financeira - DayTrade Bot
Quanto tempo para ganhar R$15.000/mês partindo de R$2.000?
"""
import math

print("=" * 70)
print("    PROJEÇÃO FINANCEIRA — DAYTRADE BOT")
print("    Capital inicial: R$ 2.000 | Meta: R$ 15.000/mês")
print("=" * 70)

# ================================================================
# DADOS REAIS DO BOT (Render.com - Produção)
# ================================================================
print("\n📊 DADOS REAIS DO BOT (Paper Trading - Render.com)")
print("-" * 50)
capital_inicial = 2000.0
total_pnl_live = 49.38
capital_efetivo = 2049.38
ciclos_rastreados = 1  # no performance tracker
ciclo_pnl = 44.43  # R$ no único ciclo rastreado
pnl_5m = -0.001
pnl_1h = 0.26
pnl_1d = 44.17

print(f"  Capital inicial:    R$ {capital_inicial:,.2f}")
print(f"  PnL acumulado:      R$ {total_pnl_live:,.2f}")
print(f"  Capital efetivo:    R$ {capital_efetivo:,.2f}")
print(f"  Retorno total:      {total_pnl_live/capital_inicial*100:.2f}%")
print(f"  Ciclos rastreados:  {ciclos_rastreados}")
print(f"  PnL melhor ciclo:   R$ {ciclo_pnl:.2f}")
print(f"    - 5m (scalping):  R$ {pnl_5m:.3f}")
print(f"    - 1h (swing):     R$ {pnl_1h:.2f}")
print(f"    - 1d (position):  R$ {pnl_1d:.2f} (99% do ganho)")
print()
print("  ⚠️  AVISO IMPORTANTE:")
print("  O bot rodou apenas ~2 horas em Paper Trading.")
print("  Amostra MUITO pequena para extrapolar com confiança.")
print("  Paper trading NÃO inclui: slippage, taxas reais,")
print("  liquidez limitada, latência de execução, spread real.")

# ================================================================
# CENÁRIOS DE RETORNO MENSAL
# ================================================================
print("\n" + "=" * 70)
print("    CENÁRIOS DE CRESCIMENTO COM JUROS COMPOSTOS")
print("    (Reinvestimento total, sem retiradas)")
print("=" * 70)

scenarios = [
    ("🟢 Conservador", 0.02,     "Fundos quant tradicionais — 2%/mês (~27%/ano)"),
    ("🟡 Moderado",    0.05,     "Bot bem calibrado em crypto — 5%/mês (~80%/ano)"),
    ("🟠 Otimista",    0.10,     "Momento favorável + boa estratégia — 10%/mês"),
    ("🔴 Agressivo",   0.15,     "Cenário excepcional + alta volatilidade — 15%/mês"),
    ("🤖 Bot paper*",  0.74,     "Extrapolação do dado paper (~2.47%/2h → 74%/mês)"),
]

meta_mensal = 15000.0

for nome, taxa_mensal, descricao in scenarios:
    print(f"\n{'─' * 60}")
    print(f"  {nome} — Taxa: {taxa_mensal*100:.0f}%/mês")
    print(f"  {descricao}")
    print(f"{'─' * 60}")
    
    # Capital necessário para gerar R$15k/mês com essa taxa
    capital_necessario = meta_mensal / taxa_mensal
    print(f"  Capital necessário para R$ 15.000/mês: R$ {capital_necessario:>12,.2f}")
    
    # Meses para atingir esse capital
    if capital_necessario <= capital_inicial:
        meses = 0
    else:
        meses = math.log(capital_necessario / capital_inicial) / math.log(1 + taxa_mensal)
    
    anos = meses / 12
    print(f"  Tempo para atingir meta: {meses:.1f} meses ({anos:.1f} anos)")
    
    # Evolução mês a mês (marcos)
    cap = capital_inicial
    marcos = [1, 3, 6, 12, 18, 24, 36, 48, 60]
    meta_atingida = False
    print(f"\n  {'Mês':>5} │ {'Capital':>14} │ {'Ganho/mês':>12} │ {'Ganho/dia':>10}")
    print(f"  {'─'*5}─┼─{'─'*14}─┼─{'─'*12}─┼─{'─'*10}")
    
    for m in range(1, 61):
        ganho_mes = cap * taxa_mensal
        cap_novo = cap + ganho_mes
        ganho_dia = ganho_mes / 30
        
        if m in marcos or (not meta_atingida and ganho_mes >= meta_mensal):
            print(f"  {m:>5} │ R$ {cap_novo:>11,.2f} │ R$ {ganho_mes:>9,.2f} │ R$ {ganho_dia:>7,.2f}")
        
        if not meta_atingida and ganho_mes >= meta_mensal:
            meta_atingida = True
            print(f"  {'':>5} │ {'>>> META R$15K/MÊS ATINGIDA! <<<':^40}")
        
        cap = cap_novo
    
    if not meta_atingida:
        # Continue beyond 60 months
        for m in range(61, 300):
            ganho_mes = cap * taxa_mensal
            cap += ganho_mes
            if ganho_mes >= meta_mensal:
                print(f"  {m:>5} │ R$ {cap:>11,.2f} │ R$ {ganho_mes:>9,.2f} │ R$ {ganho_mes/30:>7,.2f}")
                print(f"  {'':>5} │ {'>>> META R$15K/MÊS ATINGIDA! <<<':^40}")
                meta_atingida = True
                break
        if not meta_atingida:
            print(f"  {'':>5} │ {'Meta não atingida em 25 anos':^40}")

# ================================================================
# TABELA RESUMO
# ================================================================
print("\n" + "=" * 70)
print("    RESUMO — TEMPO PARA R$ 15.000/MÊS")
print("=" * 70)
print(f"\n  {'Cenário':<20} │ {'Taxa/mês':>10} │ {'Capital alvo':>14} │ {'Tempo':>12}")
print(f"  {'─'*20}─┼─{'─'*10}─┼─{'─'*14}─┼─{'─'*12}")
for nome, taxa, desc in scenarios:
    cap_alvo = meta_mensal / taxa
    if cap_alvo <= capital_inicial:
        m = 0
    else:
        m = math.log(cap_alvo / capital_inicial) / math.log(1 + taxa)
    a = m / 12
    if a >= 1:
        tempo = f"{a:.1f} anos"
    else:
        tempo = f"{m:.0f} meses"
    print(f"  {nome:<20} │ {taxa*100:>8.0f}%  │ R$ {cap_alvo:>11,.0f} │ {tempo:>12}")

# ================================================================
# CONVERSÃO USD
# ================================================================
print("\n" + "=" * 70)
print("    EQUIVALÊNCIA EM USD (câmbio R$ 5,75)")
print("=" * 70)
usd_rate = 5.75
cap_usd = capital_inicial / usd_rate
meta_usd = meta_mensal / usd_rate
print(f"  Capital inicial:  $ {cap_usd:,.2f} USD")
print(f"  Meta mensal:      $ {meta_usd:,.2f} USD")
print()
for nome, taxa, desc in scenarios:
    cap_alvo = meta_mensal / taxa
    if cap_alvo <= capital_inicial:
        m = 0
    else:
        m = math.log(cap_alvo / capital_inicial) / math.log(1 + taxa)
    cap_alvo_usd = cap_alvo / usd_rate
    print(f"  {nome:<20} │ Capital alvo: $ {cap_alvo_usd:>10,.0f} USD │ Tempo: {m:.0f} meses")

# ================================================================
# ANÁLISE DE REALISMO
# ================================================================
print("\n" + "=" * 70)
print("    ANÁLISE DE REALISMO")
print("=" * 70)
print("""
  📌 FATORES POSITIVOS DO BOT:
  ✅ 14 estratégias diversificadas (5m, 1h, 1d)
  ✅ 80+ ativos (B3 + US + Crypto + Forex + Commodities)
  ✅ Proteção inteligente (stop loss ATR, trailing, smart pause)
  ✅ Gestão de risco (Kelly Criterion, IRQ, position sizing)
  ✅ Operação 24/7 em crypto, horário comercial em ações
  ✅ Take profit parcial + ATR adaptativo

  ⚠️  FATORES DE RISCO / REDUÇÃO:
  ❌ Paper trading ≠ Trading real (diferença de 30-50%)
  ❌ Slippage em execução real (0.1-0.5% por trade)
  ❌ Taxas de corretagem (0.1% Binance, variável BTG)
  ❌ Spread bid/ask real
  ❌ Liquidez limitada para ordens maiores
  ❌ Drawdowns prolongados podem ocorrer
  ❌ Condições de mercado mudam (bull → bear → lateral)
  ❌ Risco de bugs em produção

  📊 EXPECTATIVA MAIS REALISTA:
  → O cenário MODERADO (5%/mês) é o mais provável para
    um bot bem calibrado operando em crypto com gestão de risco.
  → Isso significa ~8.6 anos para a meta com reinvestimento total.
  → Aceleradores: aportes mensais extras e otimização contínua.
""")

# ================================================================
# IMPACTO DE APORTES EXTRAS
# ================================================================
print("=" * 70)
print("    ACELERADOR: APORTES MENSAIS EXTRAS")
print("    (Cenário moderado 5%/mês + aporte mensal)")
print("=" * 70)

aportes = [0, 200, 500, 1000, 2000]
taxa = 0.05

for aporte in aportes:
    cap = capital_inicial
    for m in range(1, 300):
        ganho = cap * taxa
        cap += ganho + aporte
        if ganho >= meta_mensal:
            print(f"  Aporte R$ {aporte:>5}/mês → Meta em {m:>3} meses ({m/12:.1f} anos) │ Capital: R$ {cap:,.0f}")
            break
    else:
        print(f"  Aporte R$ {aporte:>5}/mês → Meta não atingida em 25 anos")

print("\n" + "=" * 70)
print("    CONCLUSÃO")
print("=" * 70)
print("""
  Com R$ 2.000 iniciais e reinvestimento total:

  🎯 CENÁRIO MAIS PROVÁVEL (5%/mês):
     → ~8-9 anos sem aportes extras
     → ~5-6 anos com R$ 500/mês de aporte
     → ~4 anos com R$ 1.000/mês de aporte
     → ~3 anos com R$ 2.000/mês de aporte

  🚀 CENÁRIO OTIMISTA (10%/mês):
     → ~3.8 anos sem aportes extras
     
  ⚡ CENÁRIO AGRESSIVO (15%/mês):
     → ~2.3 anos sem aportes extras

  💡 DICA: A forma mais rápida de acelerar é combinar:
     1. Aportes mensais regulares (mesmo pequenos)
     2. Otimização contínua das estratégias do bot
     3. Migração gradual de paper → trading real
     4. Adicionar capital quando tiver confiança nos resultados
""")
