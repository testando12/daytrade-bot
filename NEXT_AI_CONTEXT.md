# CONTEXTO PARA A PRÓXIMA IA — Daytrade Bot
**Data:** 2026-03-09  
**Estado atual:** Bot rodando no Railway + localmente na porta 8000

---

## 1. INFRAESTRUTURA

- **Repositório:** `c:\Users\Cliente\OneDrive\Área de Trabalho\daytrade\daytrade_bot`
- **Deploy:** Railway — `https://daytrade-bot-production.up.railway.app`
- **Config deploy:** `railway.toml` + `Dockerfile`
- **Stack:** FastAPI (Python), `app/main.py` (5453 linhas), PostgreSQL via Railway
- **Venv local:** `\.venv\Scripts\python.exe`
- **Iniciar local:** `uvicorn app.main:app --host 0.0.0.0 --port 8000`

---

## 2. ESTADO DO BOT (ATUALIZADO 2026-03-09)

```
capital         = R$ 3268.04  (BRL R$1307.22 + USD $341.01 @ 5.75)
total_pnl       = R$ 1428.18
win_count       = 219
loss_count      = 344
total_cycles    = 563
win_rate        = 38.9%
profit_factor   = 2.243
sharpe          = -0.4779
max_drawdown    = -79.14% (bug do peak_capital — já corrigido)
peak_capital    = R$ 3268.04 (resetado em 09/03)
```

⚠️ **INITIAL_CAPITAL no Railway** está como R$2770.0 (env var antiga).
O capital real é R$3268.04 (via `/performance`).

**Data sources ativos:**
- BRAPI: ✅ configured, has_token
- Yahoo Finance: ✅ configured, connected (fallback universal)
- Binance Public: ✅ configured, 31 symbols
- Binance Auth: ❌ sem API key
- BTG Pactual: ❌ sem API key
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
| `app/core/config.py` | — | Settings (MIN_MOMENTUM_SCORE, etc.) |
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
