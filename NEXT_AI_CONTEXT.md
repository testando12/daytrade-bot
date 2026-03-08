# CONTEXTO PARA A PRÓXIMA IA — Daytrade Bot
**Data:** 2026-03-06  
**Estado atual:** Bot rodando no Railway + localmente na porta 8000

---

## 1. INFRAESTRUTURA

- **Repositório:** `c:\Users\Cliente\OneDrive\Área de Trabalho\daytrade\daytrade_bot`
- **Deploy:** Railway — `https://daytrade-bot-production.up.railway.app`
- **Config deploy:** `railway.toml` + `Dockerfile`
- **Stack:** FastAPI (Python), `app/main.py` (4982 linhas), PostgreSQL via Railway
- **Venv local:** `\.venv\Scripts\python.exe`
- **Iniciar local:** `uvicorn app.main:app --host 0.0.0.0 --port 8000`

---

## 2. ESTADO DO BOT (SINCRONIZADO)

```
capital      = R$ 3289.05
total_pnl    = R$ 1449.06
win_count    = 161
loss_count   = 318
total_cycles = 479
```

Para re-sincronizar o Railway se resetar:
```bash
python _push_state_to_railway.py
```
(endpoints `/admin/restore-trade` e `/admin/restore-perf`)

---

## 3. O QUE JÁ FOI FEITO (COMPLETO ✅)

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

---

## 5. ARQUIVOS CHAVE

| Arquivo | Linhas | Descrição |
|---|---|---|
| `app/main.py` | 4982 | Toda a lógica do bot |
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
