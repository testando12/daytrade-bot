# Day Trade Bot — Documentação Completa

> Última atualização: 15 de Março de 2026

---

## 1. O QUE É

Bot automatizado de day trade que opera **24 horas por dia**, combinando múltiplas estratégias e mercados. Ele analisa dados de mercado em tempo real, identifica oportunidades com base em indicadores técnicos e executa ordens automaticamente.

Atualmente opera em **modo paper trading** (simulação com dados reais, sem enviar ordens de verdade).

---

## 2. ONDE OPERA

O bot opera em **6 mercados diferentes**, cobrindo praticamente todas as classes de ativos:

### 2.1 Criptomoedas (Binance) — 24h/dia, 7 dias/semana
- **Corretora**: Binance (API autenticada)
- **48 ativos**: BTC, ETH, BNB, SOL, ADA, XRP, DOGE, AVAX, DOT, LINK, MATIC, SHIB, UNI, LTC, ATOM, FIL, NEAR, APT, ARB, OP, INJ, SUI, SEI, TIA, PEPE, WIF, FLOKI, BONK, RENDER, FET, AAVE, MKR, COMP, CRV, SNX, JTO, PYTH, JUP, POPCAT, TON, NOT, TAO, STRK, MANTA
- **Capital alocado**: 60% do capital total (bolsão USD)

### 2.2 Ações Brasileiras — B3 (horário comercial)
- **Fonte de dados**: BRAPI (brapi.dev)
- **32 ativos**: PETR4, VALE3, ITUB4, BBDC4, BBAS3, ITSA4, GGBR4, JBSS3, SUZB3, PRIO3, CSAN3, EGIE3, ABEV3, MGLU3, LREN3, WEGE3, EMBR3, RENT3, VIVT3, RDOR3 + 12 FIIs (MXRF11, XPML11, VISC11, HGLG11, KNRI11, XPLG11, BCFF11, RBRF11, IRDM11, KNCR11)
- **Capital alocado**: 40% do capital total (bolsão BRL)

### 2.3 Ações dos EUA — NYSE/NASDAQ (13h30-20h BRT)
- **Fonte de dados**: Yahoo Finance
- **55+ ativos**: AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, AMD, JPM, NFLX, SPY, QQQ, e muitos outros
- Inclui ETFs alavancados: SOXL (semicondutores 3×), TQQQ (Nasdaq 3×), UVXY (volatilidade)

### 2.4 ETFs Internacionais (Europa, Ásia, Emergentes)
- **13 ETFs**: EWG (Alemanha), EWJ (Japão), EWY (Coreia), ASHR (China), INDA (Índia), EWZ (Brasil em USD), EEM (Emergentes), VGK (Europa)

### 2.5 Forex (câmbio)
- **10 pares**: EURUSD, GBPUSD, USDJPY, AUDUSD, USDBRL, EURBRL, e outros

### 2.6 Commodities (matérias-primas)
- **12 ativos**: Ouro, Prata, Petróleo, Gás Natural, Café, Soja, Milho, Açúcar, Trigo, Cacau, Cobre

### 2.7 Mini-Índice WIN — B3 (via MetaTrader 5 / XP Investimentos)
- **Estratégia**: ORB-30 (Opening Range Breakout dos primeiros 30 min)
- **Contrato ativo**: WINM26
- **Horário**: 09:30 → 17:00 BRT (1 trade por dia)
- **Stop**: 150 pontos | **Alvo**: 300 pontos

---

## 3. ESTRATÉGIAS (ENGINES)

O bot usa **9 estratégias** diferentes, cada uma especializada em um tipo de condição de mercado:

### 3.1 Momentum (principal)
- **O que faz**: Identifica ativos com força direcional usando ROC, RSI, EMA e volume
- **Quando opera**: Mercado em tendência (ADX > 25)
- **Score mínimo**: 0.55 (só entra com sinais fortes)
- **Win rate alvo**: ≥ 70%

### 3.2 Breakout (rompimento)
- **O que faz**: Detecta rompimentos de suporte/resistência com confirmação de volume
- **Quando opera**: Preço rompe máxima/mínima com volume ≥ 1.5× a média
- **Filtro**: Rompimento mínimo de 0.5% (evita sinais falsos)

### 3.3 Mean Reversion (reversão à média)
- **O que faz**: Compra quando preço está muito abaixo da média (oversold), vende acima (overbought)
- **Indicadores**: Bollinger Bands (2σ e 1.5σ), RSI ≤ 35 para compra
- **Quando opera**: Mercado lateral (ADX < 20)

### 3.4 Squeeze (compressão de volatilidade)
- **O que faz**: Detecta compressão extrema de volatilidade seguida de explosão direcional
- **Método**: Bollinger Bands dentro do Keltner Channel = squeeze confirmado
- **Win rate esperado**: 35-45% | **R/R**: 2.5-4.0

### 3.5 Fair Value Gap (FVG)
- **O que faz**: Identifica gaps de preço institucionais (zonas de desequilíbrio)
- **Lógica**: Candle central forte que deixa "lacuna" entre candles adjacentes
- **Quando opera**: Preço retorna à zona do gap → entrada na direção do preenchimento

### 3.6 Liquidity Sweep (caça de liquidez)
- **O que faz**: Detecta quando grandes players "varrem" stops antes de reverter
- **Lógica**: Preço rompe uma máxima/mínima, ativa stops, depois reverte rapidamente
- **Diferença do Breakout**: Busca o RETORNO após o rompimento falso

### 3.7 VWAP Reversion
- **O que faz**: Reversão ao preço médio ponderado por volume
- **Regra**: Compra se preço < VWAP - 1.5σ + RSI < 30 | Vende se preço > VWAP + 1.5σ + RSI > 70
- **Win rate esperado**: 55-65% | **R/R**: 1.2-1.8

### 3.8 Pyramid Breakout (piramidagem)
- **O que faz**: Captura movimentos grandes de tendência, aumentando posição conforme confirma
- **Piramidagem**: posição base → +50% a +1 ATR → +30% a +2 ATR (máx 180%)
- **Perfil**: Muitos trades pequenos perdendo, poucos trades gigantes pagando tudo

### 3.9 ORB-30 WIN (Mini-Índice B3)
- **O que faz**: Opening Range Breakout nos primeiros 30 minutos do pregão
- **Backtest**: WR=51.7% | Fator=2.02 | Retorno=+89% (Dez/25 a Mar/26)
- **Limite**: 1 trade por dia, fecha às 17h

---

## 4. GESTÃO DE RISCO

### 4.1 Stop Loss e Take Profit
| Parâmetro | Valor |
|---|---|
| Stop Loss padrão | 1.2% |
| Take Profit padrão | 3.5% |
| Trailing Stop | 0.8% abaixo do pico |
| **ATR Adaptativo** | SL = ATR × 1.2 / TP = ATR × 4.0 |
| SL mínimo | 0.5% |
| SL máximo | 4.0% |
| Risk:Reward mínimo | 1:2.9 |

### 4.2 Limites Operacionais
| Proteção | Limite |
|---|---|
| Perda diária máxima | 5% → PAUSA automática |
| Perda semanal máxima | 10% → opera com 25% do tamanho |
| Max drawdown | 10% do capital inicial → HARD STOP |
| Perdas consecutivas | 3 → reduz tamanho 50% |
| Trades por hora | máx 120 |
| Trades por dia | máx 1.000 |

### 4.3 Limite por Setor (diversificação)
| Setor | Exposição máxima |
|---|---|
| Crypto | 40% do capital |
| B3 | 35% do capital |
| US Stocks | 35% do capital |
| ETFs Internacionais | 20% do capital |
| Commodities | 20% do capital |
| Forex | 15% do capital |

### 4.4 Por Posição
- Máximo 30% do capital em um único ativo
- Posição mínima: R$30 (Binance: US$5)
- Correlação: máximo 1 entrada por cluster de ativos correlacionados por ciclo

### 4.5 Regime de Mercado (adaptativo)
O bot detecta automaticamente o regime atual e ajusta as estratégias:
| Regime | Detecção | Ação |
|---|---|---|
| TREND_STRONG | ADX > 25 + Hurst > 0.52 | Boost para Breakout/Momentum |
| TREND_WEAK | ADX > 20 + Hurst > 0.50 | Boost leve para trend-following |
| LATERAL | ADX < 20 + Hurst < 0.52 | Boost para Mean Reversion/Squeeze |
| HIGH_VOL | ATR ratio > 1.6 | Reduz tamanho de todas as posições |
| LOW_VOL_SQUEEZE | ATR ratio < 0.65 | Boost para Squeeze |

---

## 5. FUNCIONALIDADES EXTRAS

### 5.1 Grid Trading (mercado lateral)
- 5 níveis de compra/venda com espaçamento de 0.5%
- Usa 20% do capital
- Ativa quando volatilidade ≥ 1%

### 5.2 Scalping Turbo
- Ativa quando volatilidade > 1.5%
- Ciclos rápidos de 2 minutos
- Take profit rápido de 0.4%

### 5.3 Partial Take Profit
- Realiza 35% do lucro no primeiro alvo (1.2%)
- Deixa 65% da posição correr para lucros maiores

### 5.4 Momentum Acceleration
- Detecta aceleração de momentum > 2% entre ciclos
- Aumenta posição 50% quando confirmado

### 5.5 Kelly Criterion (dimensionamento)
- Usa Kelly Fracionário (25% do Kelly teórico) para dimensionar posições
- Ajusta dinamicamente baseado no win rate e risk:reward atuais

### 5.6 Compounding (juros compostos)
- Reinveste 100% do lucro diário automaticamente
- Capital cresce exponencialmente com sequência de wins

---

## 6. CONTAS CONECTADAS

| Conta | Uso | Modo Atual |
|---|---|---|
| **Binance** | Crypto (BTC, ETH, etc.) | Paper Trading (simulação) |
| **XP / MetaTrader 5** | Mini-Índice WIN (B3) | Paper Trading (simulação) |
| **BRAPI** | Dados de ações brasileiras | Leitura de dados |
| **Yahoo Finance** | US Stocks, Forex, Commodities | Leitura de dados |

> As contas operam **independentemente**. O bot não transfere dinheiro entre elas.

---

## 7. ESTADO ATUAL (15/Mar/2026)

| Métrica | Valor |
|---|---|
| Capital total registrado | R$ 610,21 |
| Capital crypto (Binance paper) | US$ 91,52 |
| Posições Binance abertas | 20 |
| Ordens Binance executadas | 96 |
| Total de ciclos rodados | 369 |
| PnL total registrado | R$ 108,12 |
| Status | **HARD STOP** (drawdown 65% do pico) |
| Auto trading | Ativo |
| Win Rate (Kelly dinâmico) | 71.5% (206 wins / 288 trades) |
| Regime atual | TREND_STRONG (ADX=42.1) |

### Status do HARD STOP:
O bot atingiu um drawdown de 65% em relação ao pico de capital (R$ 610,21 → R$ 213,57). O mecanismo de proteção pausou automaticamente as operações. É necessário reset manual para retomar.

---

## 8. CICLO DE OPERAÇÃO

```
A cada 10 minutos (crypto) / 10 minutos (B3):

1. Coleta dados de mercado (preços, volumes, candles)
2. Detecta regime de mercado (tendência, lateral, volátil)
3. Roda todas as 9 engines de análise
4. Filtra sinais por momentum score ≥ 0.55
5. Verifica limites de risco (exposição, setor, drawdown)
6. Calcula tamanho da posição (Kelly + regime + ATR)
7. Executa ordens (paper ou real)
8. Atualiza stops e take profits das posições abertas
9. Registra resultados em performance.json
10. Envia alertas (Telegram/Discord, se configurado)
```

---

## 9. INFRAESTRUTURA

| Componente | Tecnologia |
|---|---|
| Linguagem | Python 3 |
| Framework Web | FastAPI |
| Banco de dados | SQLite |
| Deploy | Local (Windows) + Railway (mirror/dashboard) |
| Dashboard | Web (HTML/JS) acessível pelo celular |
| Alertas | Telegram, Discord (opcionais) |

### Dashboard Railway
- URL: https://daytrade-bot-production.up.railway.app
- Função: Espelho do bot local para visualização no celular
- O bot roda localmente e empurra o estado para Railway a cada ciclo

---

## 10. CONFIGURAÇÃO RESUMIDA (.env)

```
# Chaves de API
BINANCE_API_KEY=***       → Crypto
BINANCE_API_SECRET=***    → Crypto
MT5_LOGIN=***             → Mini-Índice WIN
MT5_PASSWORD=***          → Mini-Índice WIN
BRAPI_TOKEN=***           → Ações B3

# Capital
INITIAL_CAPITAL=450       → Capital inicial em R$
TRADING_MODE=paper        → paper (simulação) | live (real)

# Divisão de capital
CAPITAL_BRL_PCT=0.40      → 40% para B3
CAPITAL_USD_PCT=0.60      → 60% para Crypto/US
```

---

## 11. MODOS DE OPERAÇÃO

| Modo | Descrição | Risco |
|---|---|---|
| **paper** (atual) | Dados reais, ordens simuladas | Zero — não mexe em dinheiro real |
| **testnet** | API real da Binance com dinheiro fictício | Zero — testnet não é real |
| **live** | Ordens reais enviadas à corretora | **REAL** — dinheiro de verdade |

> **Recomendação**: Só mude para `live` quando tiver confiança nos resultados do paper trading por pelo menos 30 dias consecutivos.

---

*Documento gerado automaticamente em 15/03/2026 pelo GitHub Copilot.*
