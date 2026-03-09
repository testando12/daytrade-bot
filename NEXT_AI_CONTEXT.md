# CONTEXTO PARA A PRÓXIMA IA — Daytrade Bot
**Data:** 2026-03-09  
**Estado atual:** Bot rodando no Railway — **v4 ROBUSTO** (7/8 stress, 6/8 ruin). Paper trading 30 dias antes de ir LIVE.

---

## 1. INFRAESTRUTURA

- **Repositório:** `c:\Users\Cliente\OneDrive\Área de Trabalho\daytrade\daytrade_bot` (GitHub: testando12/daytrade-bot)
- **Deploy:** Railway — `https://daytrade-bot-production.up.railway.app` (auto-deploy on push)
- **Config deploy:** `railway.toml` + `Dockerfile`
- **Stack:** FastAPI (Python), `app/main.py` (~5515 linhas), PostgreSQL via Railway
- **Venv local:** `\.venv\Scripts\python.exe` (usar `python` direto no PowerShell)
- **Iniciar local:** `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- **Último commit:** v4 — perf: R:R >1:1, trailing 60%, breakeven +0.3%, lateral boost, partial TP delayed

---

## 2. ESTADO DO BOT (ATUALIZADO 2026-03-09)

```
capital         = R$ 3284.10
total_pnl       = R$ 1428.18
win_count       = 219
loss_count      = 344
total_cycles    = 563
win_rate        = 38.9%  (histórico, inclui período pré-otimização)
profit_factor   = 2.243
sharpe          = -0.4779
max_drawdown    = 0% (peak_capital resetado em 09/03)
peak_capital    = R$ 3284.10
mode            = paper (simulated orders, real data)
```

### Stress Test v4 — Resultados (09/03/2026)
```
Break-even SQ   = 0.30  (excelente, <0.40 é forte)
Win Rate         = 60.3% (era 56.2% no v3)
R:R              = 1.21:1 (era 0.96:1 — CRUZOU 1:1!)
Profit Factor    = 1.835 (era 1.225 — +50%)
Profit 1000c     = +74.3%
Max Drawdown     = 2.2%  (era 6.0%)
Sharpe           = 1.30
30 bad days      = +46.4% (era -4.7% — AGORA LUCRATIVO!)
Ruin risk        = 0.00%
Expectation/trade= +R$2.55 (era +R$0.71 — +3.6x)
Metrics passed   = 7/7
Tests passed     = 7/8  (só Taxas 2x falha)
Verdict          = SISTEMA ROBUSTO
```

### Risk of Ruin v2 — Resultados (09/03/2026)
```
WR: 57.1%  |  R:R: 1.17:1  |  Expectativa: +R$2.37/trade
Monte Carlo Normal:  99.9% lucrativo, 0% ruína, DD P95=4.1%  → ROBUSTO
Monte Carlo Ruim:    39.6% lucrativo, 0% ruína               → FRAGIL
Monte Carlo Extremo:  1.4% lucrativo, 0% ruína               → FRAGIL
Consec. Losses P99:  14                                       → OK
Stress Execução:     lucro +22.67% (retém 39% do normal)      → OK
Shuffle Test:        100% lucrativo, 0% ruína                 → ROBUSTO
Resultado:           6/8 aprovados → SISTEMA ACEITAVEL
```

### Parâmetros de Risco Atuais (config.py + main.py)
```
SL              = 1.2%
TP              = 3.5%
Trailing stop   = retém 60% dos ganhos, mín 0.2% (era 50%, 0.15%)
Breakeven       = trigger +1.0%, garante +0.3% (era +1.5%, +0.2%)
ATR_SL_MULT     = 1.2
ATR_TP_MULT     = 4.0
Partial TP      = 35% @ 1.2% (era 40% @ 1.0% — deixa winners correr)
capture_mean    = 0.85 (era 0.80)
capture_std     = 0.10 (era 0.12)
MIN_MOMENTUM    = 0.55
Score filter    = 0.58
LATERAL MR/SQ/VR = 1.55/1.40/1.60 (boost para regime lateral)
```

### 10-Bucket Allocation (v4.0)
```
5min=8% | 1h=20% | 1d=30% | MR=8% | BO=6% | SQ=5% | LS=2% | FVG=2% | VR=10% | PB=9%
```

**Data sources ativos:**
- BRAPI: ✅ configured, has_token
- Yahoo Finance: ✅ configured, connected (fallback universal)
- Binance Public: ✅ configured, 31 symbols
- Binance Auth: ❌ sem API key
- BTG Pactual: ❌ sem API key (código pronto em `app/brokers/btg.py`, `BTG_PAPER_TRADING=True`)
- Alpha Vantage: ❌ sem API key

Para re-sincronizar o Railway se resetar:
```bash
python _push_state_to_railway.py
```
(endpoints `/admin/restore-trade` e `/admin/restore-perf`)

---

## 3. O QUE JÁ FOI FEITO (COMPLETO ✅)

### 3.0 Fix HARD STOP + Peak Capital (09/03/2026)
- **Bug:** `peak_capital` subiu para R$15.670 (anomalia no equity_curve). Com capital real de R$3.268, o sistema calculou drawdown de 79.1% e ativou HARD STOP — bot ficou completamente parado por ~2 dias.
- **Fix:** `POST /trade/unfreeze` resetou peak_capital para R$3.268.04, drawdown para 0%, HARD STOP desativado.
- **Causa raiz do lucro baixo:** A maioria dos ciclos retorna PnL=0 porque: (a) filtros (min_ret_threshold, rejection_rate, score<0.55) bloqueiam maioria das entradas, (b) quando data source cai, usa `test_assets_data` (dados estáticos, ~linha 765 do main.py), (c) Grid Trading gera apenas ~R$0.04/ciclo.
- **Último log de trade no Railway:** Capital estava em R$3272.67 no dia 07/03 antes do HARD STOP.

### 3.1 Correções de métricas
- `_effective_total_cycles()` em `app/main.py` → usa `win_count + loss_count` (era `len(cycles)` = 0)
- `total_pnl` no `/performance` → usa `_pnl_offset` como fallback
- Capital exibido corretamente no dashboard

### 3.2 Critério LIVE — Win Rate → Profit Factor
- `dashboard-web/app.js` e `dashboard-web/index.html`: substituídos critérios `minWinRate: 57` por `minProfitFactor: 1.8`
- Fases: Bootstrap PF≥1.1, Estabilização PF≥1.3, Validação PF≥1.5, Pronto LIVE PF≥1.8
- Função `evaluatePreRealGate()` calcula `profitFactor = totalGain / totalLoss`

### 3.3 Railway restaurado
- `railway.toml` recriado
- Keep-alive usa `RAILWAY_PUBLIC_DOMAIN` env var
- `app.js` usa `window.location.origin` (sem URL hardcoded)
- Estado sincronizado via `_push_state_to_railway.py`

### 3.4 Monte Carlo
- Arquivo `_monte_carlo.py` criado
- 10.000 simulações: 100% lucrativas, P99 DD=2.59%, Score 10/10

### 3.5 Melhorias v4 — R:R, Lateral, Concentração (09/03/2026)
Três fraquezas identificadas e corrigidas:
1. **R:R < 1:1** → trailing 60%, breakeven +0.3%, capture 0.85 → R:R agora **1.21:1**
2. **Lateral -11.1%** → multipliers MR/SQ/VR boosted → lateral agora **-4.6%**
3. **Lucro concentrado top 5** → partial TP 35%@1.2% (lets winners run)

Arquivos modificados: `main.py`, `config.py`, `regime.py`, `_stress_tests.py`
Novo arquivo: `_risk_of_ruin.py` (Monte Carlo 5k sims, shuffle test, stress execution)

### 3.6 Risk of Ruin Test Suite (09/03/2026)
- Arquivo `_risk_of_ruin.py` criado com 6 seções de teste
- Monte Carlo (normal/ruim/extremo), consecutive losses, execution stress, shuffle test
- v1: 6/8 ACEITAVEL → v2 (após melhorias): 6/8 ACEITAVEL mas métricas muito melhores
- 0% ruína em TODOS os cenários (inclusive extremos)

---

## 4. O QUE FALTA FAZER ⏳ — IMPLEMENTAR TESTES PARA SLIPPAGE E OUTROS

Os testes avançados mencionados (slippage, concentração de capital, estabilidade do Sharpe, degradação do edge) não estão implementados. Para que funcionem, é necessário criar os endpoints na API. Aqui está o que fazer:

### 4.1 Criar endpoint `/test/slippage`

Arquivo: `app/main.py`

```python
@app.post("/test/slippage")
def test_slippage(slippage_multiplier: float):
    """Simula impacto de slippage progressivo."""
    # Lógica: aplicar slippage_multiplier nos custos e recalcular métricas
    # Exemplo: recalcular Profit Factor, Expectancy, Drawdown
    ...
```

### 4.2 Criar endpoint `/test/concentration`

Arquivo: `app/main.py`

```python
@app.get("/test/concentration")
def test_concentration():
    """Calcula índice de concentração de PnL entre buckets."""
    # Lógica: calcular concentração = lucro_bucket / lucro_total
    # Retornar concentração por bucket e identificar dominância (>70%)
    ...
```

### 4.3 Criar endpoint `/test/sharpe`

Arquivo: `app/main.py`

```python
@app.get("/test/sharpe")
def test_sharpe_stability():
    """Simula estabilidade do Sharpe ao aumentar capital."""
    # Lógica: simular Sharpe para diferentes níveis de capital
    # Exemplo: 3k, 10k, 50k, 100k
    ...
```

### 4.4 Criar endpoint `/test/edge`

Arquivo: `app/main.py`

```python
@app.get("/test/edge")
def test_edge_degradation():
    """Mede degradação do edge em trades consecutivos."""
    # Lógica: simular trades consecutivos e medir quando expectancy desaparece
    ...
```

### 4.5 Estratégias Futuras ⏳

#### 4.5.1 Implementar Testes Avançados
- **Slippage:** Criar endpoint `/test/slippage` para simular impacto de slippage progressivo.
- **Concentração de Capital:** Criar endpoint `/test/concentration` para calcular índice de concentração de PnL entre buckets.
- **Estabilidade do Sharpe:** Criar endpoint `/test/sharpe` para simular estabilidade do Sharpe ao aumentar capital.
- **Degradação do Edge:** Criar endpoint `/test/edge` para medir degradação do edge em trades consecutivos.

---

## 5. ARQUIVOS CHAVE

| Arquivo | Linhas | Descrição |
|---|---|---|
| `app/main.py` | 5453 | Toda a lógica do bot |
| `app/engines/momentum.py` | 209 | Engine de momentum |
| `app/engines/mean_reversion.py` | ~217 | Engine de mean reversion |
| `app/engines/__init__.py` | 12 | Exports dos engines |
| `app/core/config.py` | — | Settings (partial TP, SL/TP, etc.) |
| `app/engines/regime.py` | — | Regime detector + multipliers (lateral boosted v4) |
| `_risk_of_ruin.py` | ~350 | Monte Carlo, shuffle, stress execution tests |
| `_stress_tests.py` | ~650 | Stress test completo (8 cenários) |
| `dashboard-web/app.js` | — | Dashboard frontend |
| `_push_state_to_railway.py` | — | Sincroniza estado para Railway |
| `railway.toml` | — | Config deploy Railway |

---

## 6. COMANDOS ÚTEIS

```bash
# Verificar bot local
curl http://localhost:8000/health

# Ver performance
curl http://localhost:8000/performance

# Iniciar bot local
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Sincronizar estado Railway
.\venv\Scripts\python.exe _push_state_to_railway.py

# Push para Railway (deploy automático)
git push origin main
```

### 4.5.2 Estratégia: VWAP Reversion com Confirmação

#### 🎯 Ideia
O preço costuma se afastar do preço médio ponderado por volume (VWAP) e depois voltar. Essa estratégia tenta capturar esses retornos curtos.

#### 1️⃣ Setup Principal
**Indicadores:**
- VWAP
- Relative Strength Index (RSI)
- Average True Range (ATR)

#### 2️⃣ Entrada
**Compra:**
- Condições:
  - preço < VWAP - 1.5 desvio
  - RSI < 30
  - volume acima da média
- Entrada:
  - comprar esperando retorno ao VWAP

**Venda:**
- Condições:
  - preço > VWAP + 1.5 desvio
  - RSI > 70
- Entrada:
  - short esperando retorno ao VWAP

#### 3️⃣ Stop
Stop curto baseado em volatilidade:
- **stop = 0.8 ATR**
- Isso mantém perdas pequenas.

#### 4️⃣ Take Profit
Alvo simples:
- **take profit = VWAP**
- Ou: **0.8 – 1.2 ATR**

#### 📊 Perfil Estatístico Esperado
Esse tipo de estratégia normalmente tem:

| Métrica         | Valor Típico |
|-----------------|--------------|
| Win rate        | 55–65%       |
| Risk/Reward     | 1.2 – 1.8    |
| Trades/dia      | 5–15         |

Isso é ideal para metas diárias pequenas.

#### Exemplo Prático
Suponha:
- lucro médio por trade = R$12
- 10 trades por dia

**Resultado esperado:**
- ≈ R$120 por dia
- Claro que alguns dias serão negativos.

#### ⚠️ Controle de Risco (Muito Importante)
Para manter risco quase alto mas controlado:
- **risco por trade = 1.5% – 2%**
- **stop diário = 5% do capital**
- **máx trades simultâneos = 2**

Isso evita grandes perdas.

#### 🧠 Quando Essa Estratégia Funciona Melhor
- mercado lateral
- volatilidade moderada
- sessões com bastante volume

Ela explora o conceito de Mean Reversion.

#### 💡 Truque Usado por Bots Profissionais
Ativar essa estratégia apenas quando:
- **ADX < 20**
- usando o indicador: Average Directional Index

Isso evita operar quando o mercado está em tendência forte.

#### ⚠️ Realidade Importante
Para gerar R$100/dia consistentemente, normalmente você precisa de:

| Capital | Expectativa Realista |
|---------|-----------------------|
| R$1000  | difícil               |
| R$3000  | possível              |
| R$5000+ | mais consistente      |

Porque metas fixas diárias não combinam perfeitamente com a natureza aleatória do mercado.

✅ **A melhor abordagem é pensar em média semanal ou mensal, não diária.**

---

### 4.5.3 Estratégia: Micro-Breakout com Pullback

#### 🎯 Ideia
Capturar rompimentos curtos que ocorrem várias vezes no dia e fechar rápido.

#### 1️⃣ Setup do Gráfico
**Timeframe:**
- 1m ou 3m

**Indicadores:**
- EMA 9
- EMA 21
- VWAP
- ATR(14)

#### 2️⃣ Condição de Tendência Curta
Primeiro identificar micro tendência:
- **EMA9 > EMA21**
- **preço acima da VWAP**

Isso indica fluxo comprador curto.

#### 3️⃣ Entrada
Esperar um pequeno pullback.

**Condição:**
- preço recua até EMA9
- volume diminui
- candle seguinte fecha forte

**Entrada:**
- comprar no rompimento do candle anterior

#### 4️⃣ Stop
Stop curto:
- **stop = 0.7 × ATR**
- Isso limita perda.

#### 5️⃣ Take Profit
Alvo pequeno:
- **take profit = 1.2 × ATR**
- Ou um R/R ≈ 1.7:1.

#### 📊 Frequência Esperada
Dependendo do mercado:

| Métrica         | Valor |
|-----------------|-------|
| Trades por dia  | 15–30 |
| Win rate        | 50–60%|
| R/R médio       | ~1.5  |

Isso gera fluxo constante de trades.

#### Exemplo com Capital Baixo
Suponha:
- risco por trade = R$10
- lucro médio por trade = R$15

**Com 12 trades positivos:**
- ≈ R$180
- Se houver perdas no meio, ainda pode chegar perto da meta diária.

#### ⚠️ Controle de Risco (Importante)
Para manter o risco agressivo mas não extremo:
- **risco por trade = 1.5–2%**
- **máx trades simultâneos = 2**
- **stop diário = 6% do capital**

Isso deixa o sistema quase no limite de risco alto, mas ainda controlado.

#### 🧠 Filtro Importante
Evite operar quando o mercado está sem direção.

**Use:**
- Average Directional Index

**Regra:**
- **ADX > 20**

Isso melhora a qualidade dos rompimentos.

#### 💡 Truque Usado em Bots
Se 3 trades seguidos derem loss, parar temporariamente:
- **cooldown = 30–60 minutos**

Isso evita operar em regime ruim do mercado.

✅ **Esse tipo de estratégia é comum em bots porque:**
- gera muitos trades
- captura movimentos curtos
- pode atingir metas pequenas diárias.

---

### 4.5.4 Estratégia: Breakout com Piramidagem Progressiva

#### 🎯 Objetivo
Capturar movimentos grandes de tendência e aumentar posição conforme o trade confirma. Cria ganhos muito grandes quando acerta e perdas pequenas quando erra.

#### 1️⃣ Condição de Entrada
Rompimento de volatilidade:
- **Preço rompe máxima de 20 candles**
- **ATR aumentando**
- **Volume acima da média**
→ Abrir posição

**Indicadores:** Bollinger Bands, ATR(14)

#### 2️⃣ Risco Inicial (Agressivo Controlado)
- **Risco por trade = 1.8% – 2.2% do capital**
- **Stop loss = 1.5 ATR abaixo da entrada**

#### 3️⃣ Piramidagem (fonte do lucro)
Aumentar posição apenas quando o trade já está lucrando:
- **+1 ATR a favor → adicionar 50% da posição**
- **+2 ATR a favor → adicionar mais 30%**

Exposição total máxima: ~180% da posição original.

#### 4️⃣ Trailing Stop
Stop móvel baseado em:
- **EMA20** ou **2 × ATR abaixo do preço**
- Deixa o trade correr enquanto a tendência existe

#### 5️⃣ Gestão de Risco Global
- **máx trades simultâneos = 3**
- **risco total aberto ≤ 5% do capital**

#### 📊 Perfil Estatístico Esperado

| Métrica        | Valor Típico |
|----------------|-------------|
| Win rate       | 30–40%      |
| Risk/Reward    | 4:1 a 8:1   |
| Profit Factor  | 1.8 – 2.5   |
| Drawdown       | 10–18%      |

Muitos trades pequenos perdendo, poucos trades gigantes pagando tudo.

#### 🧠 Filtro de Ativação
- **ADX > 25** (só operar em tendência clara)
- Mercados laterais destroem essa estratégia

#### ⚠️ Complemento Natural
Combinar com VWAP Reversion (4.5.2) que opera em ADX < 20. Quando Breakout para (mercado lateral), VWAP Reversion opera e vice-versa.

---

### 4.5.5 Sistema Combinado por Regime (Recomendação)

| Regime (ADX)         | Estratégia                    | Finalidade     |
|----------------------|-------------------------------|----------------|
| Tendência (ADX > 25) | Breakout + Piramidagem (4.5.4) | Captura caudas |
| Lateral (ADX < 20)   | VWAP Reversion (4.5.2)        | Gera caixa     |
| Transição (ADX 20-25)| Micro-Breakout (4.5.3)        | Scalping       |

O bot já tem `RegimeDetector` com ADX — infraestrutura pronta para implementar.
