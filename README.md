# Day Trade Bot - Sistema Automatizado de Trading

Sistema automatizado de day trading baseado em análise de **Momentum** e **Risco** (IRQ), sem IA complexa - apenas regras inteligentes de trading.

## 🎯 Características Principais

### 1. **Motor de Análise de Momentum**
- Calcula Momentum Score baseado em:
  - **Retorno Percentual** (50% do peso)
  - **Tendência de Médias Móveis** (30% do peso)
  - **Força de Volume** (20% do peso)
- Classifica ativos em: FORTE_ALTA, ALTA_LEVE, LATERAL, QUEDA

### 2. **Sistema de Risco (IRQ - Índice de Risco de Queda)**
- Detecta 5 sinais de queda iminente:
  - S1: Perda de tendência
  - S2: Pressão vendedora com volume
  - S3: Volatilidade elevada
  - S4: Divergência RSI
  - S5: Sequência de quedas
- Calcula probabilidade real de queda (0-100%)
- Ativa modo de proteção automático quando risco > 70%

### 3. **Alocação Dinâmica de Capital**
- Distribui capital entre ativos baseado em momentum
- Aplica limites de proteção:
  - Máximo 30% do capital por ativo
  - Mínimo R$10 por posição
  - Stop loss de 5%
- Rebalanceia automaticamente a cada 5 minutos

### 4. **Regras Automáticas de Operação**
```
Se FORTE_ALTA:     aumentar posição 20%
Se ALTA_LEVE:      manter posição
Se LATERAL:        reduzir para mínimo
Se QUEDA:          reduzir posição 50%
```

### 5. **Proteção Global**
```
IRQ ≤ 70%:  operação normal
IRQ 70-80%: reduzir 40% das posições
IRQ 80-90%: reduzir 70% das posições
IRQ > 90%:  sair totalmente do mercado
```

## 📊 Arquitetura

```
┌─────────────────────────────────────┐
│       API FastAPI (Port: 8000)      │
├─────────────────────────────────────┤
│   Endpoints:                        │
│   - /analyze/momentum               │
│   - /analyze/risk                   │
│   - /analyze/full                   │
│   - /status                         │
│   - /config                         │
└────────┬────────────────────────────┘
         │
    ┌────┴─────────────────────────────────┐
    │                                       │
    ▼                                       ▼
┌─────────────────┐            ┌──────────────────┐
│   Engine        │            │  Database        │
│ Momentum        │            │ SQLAlchemy       │
│ Risk (IRQ)      │            │ PostgreSQL       │
│ Portfolio       │            │                  │
└─────────────────┘            └──────────────────┘
    │
    ▼
┌──────────────────────┐
│  Market Data APIs    │
│  (Binance, Polygon)  │
└──────────────────────┘
```

## 🚀 Quick Start

### 1. **Instalação**

```bash
# Clone o repositório
cd daytrade_bot

# Crie um ambiente virtual
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Instale as dependências
pip install -r requirements.txt
```

### 2. **Configuração**

```bash
# Copie o arquivo de exemplo
cp .env.example .env

# Configure o .env com seus dados:
INITIAL_CAPITAL=150
DATABASE_URL=postgresql://user:password@localhost:5432/daytrade_db
```

### 3. **Rode o Servidor**

```bash
# Inicie a aplicação FastAPI
python -m uvicorn app.main:app --reload --port 8000
```

A API estará disponível em: `http://localhost:8000`

### 4. **Teste o Bot**

Acesse a documentação interativa: `http://localhost:8000/docs`

**Endpoints disponíveis:**

```bash
# Análise de Momentum
curl http://localhost:8000/analyze/momentum

# Análise de Risco (IRQ)
curl http://localhost:8000/analyze/risk

# Análise Completa (Momentum + Risco + Alocação)
curl http://localhost:8000/analyze/full

# Status do Bot
curl http://localhost:8000/status

# Configurações
curl http://localhost:8000/config
```

## 🧪 Backtesting

Teste a estratégia com dados históricos simulados:

```bash
python backtest.py
```

Isso gera um arquivo `backtest_report.json` com:
- Retorno total
- Drawdown máximo
- Sharpe ratio
- Histórico detalhado

## 📁 Estrutura do Projeto

```
daytrade_bot/
├── app/
│   ├── __init__.py
│   ├── main.py                 # Aplicação FastAPI principal
│   ├── core/
│   │   ├── config.py           # Configurações
│   │   └── database.py         # Conexão com DB
│   ├── engines/
│   │   ├── momentum.py         # Análise de Momentum
│   │   ├── risk.py             # Análise de Risco (IRQ)
│   │   └── portfolio.py        # Gerenciamento de Portfólio
│   ├── models/
│   │   └── database.py         # Modelos SQLAlchemy
│   ├── schemas/
│   │   └── schemas.py          # Schemas Pydantic
│   └── api/
│       └── (rotas adicionais)
├── tests/
│   └── (testes unitários)
├── data/
│   └── (dados históricos)
├── backtest.py                 # Script de backtesting
├── requirements.txt            # Dependências
├── .env.example               # Exemplo de configuração
└── README.md                  # Este arquivo
```

## 📈 Exemplo de Resposta da API

### Análise Completa (`/analyze/full`)

```json
{
  "success": true,
  "message": "Análise completa concluída",
  "data": {
    "timestamp": "2024-02-23T10:30:00",
    "momentum_analysis": {
      "BTC": {
        "momentum_score": 0.45,
        "trend_status": "alta",
        "classification": "FORTE_ALTA",
        "return_pct": 3.2
      },
      "ETH": {
        "momentum_score": -0.15,
        "trend_status": "lateral",
        "classification": "LATERAL",
        "return_pct": -0.5
      }
    },
    "risk_analysis": {
      "irq_score": 0.35,
      "level": "NORMAL",
      "color": "🟢",
      "reduction_percentage": 0.0,
      "rsi": 65.2
    },
    "allocations": {
      "BTC": {
        "action": "BUY",
        "recommended_amount": 60.0,
        "change_percentage": 20.0
      },
      "ETH": {
        "action": "HOLD",
        "recommended_amount": 45.0,
        "change_percentage": 0.0
      }
    },
    "capital_info": {
      "total_capital": 150.0,
      "cash_available": 45.0,
      "active_positions": 2
    }
  }
}
```

## 🛡️ Proteção e Segurança

✅ **Stop Loss**: Saia automaticamente se perda > 5%
✅ **Limite por Ativo**: Máximo 30% do capital em um ativo
✅ **Proteção de Risco Global**: Modo defensivo quando mercado está em risco
✅ **Limite de Operações**: Rebalanceia a cada 5 minutos
✅ **Diversificação**: Distribui entre múltiplos ativos

## 🔄 Fluxo de Operação

1. **Coleta de Dados** → Obter preços e volumes em tempo real
2. **Análise de Momentum** → Calcular força de cada ativo
3. **Análise de Risco** → Calcular IRQ do mercado
4. **Alocação de Capital** → Distribuir recursos
5. **Rebalanceamento** → Ajustar posições de acordo com regras
6. **Execução** → Enviar ordens ao exchange/corretora
7. **Monitoramento** → Rastrear P&L e atualizar dashboard

## 📊 Parâmetros Configuráveis

| Parâmetro | Padrão | Descrição |
|-----------|--------|-----------|
| INITIAL_CAPITAL | R$150 | Capital inicial |
| MAX_POSITION_PERCENTAGE | 30% | Máximo por ativo |
| MIN_POSITION_AMOUNT | R$10 | Mínimo por posição |
| STOP_LOSS | 5% | Stop loss automático |
| REBALANCE_INTERVAL | 300s | Intervalo de rebalanceamento |
| IRQ_THRESHOLD_HIGH | 0.70 | Início de proteção |
| IRQ_THRESHOLD_CRITICAL | 0.90 | Sair do mercado |

## 🎓 Conceitos Matemáticos

### Momentum Score
```
M_i = 0.5 * R_i + 0.3 * T_i + 0.2 * V_i

Onde:
R_i = Retorno percentual
T_i = Tendência (média curta - média longa)
V_i = Volume relativo
```

### IRQ (Índice de Risco de Queda)
```
IRQ = 0.25*S1 + 0.25*S2 + 0.15*S3 + 0.15*S4 + 0.20*S5

Onde:
S1 = Perda de tendência
S2 = Pressão vendedora
S3 = Volatilidade
S4 = Divergência RSI
S5 = Sequência de quedas
```

### Alocação Final
```
A_i = C * (M_i / ΣM_j) * (1 - IRQ)

Onde:
C = Capital total
M_i = Momentum do ativo i
IRQ = Risco global
```

## ⚠️ Avisos Importantes

⚠️ **USE POR SUA CONTA E RISCO**
- Este bot é para fins educacionais
- Sempre teste em ambiente de sandbox/papertrading
- Comece com pequenos valores de capital
- Monitore o bot regularmente
- Nenhuma garantia de lucro

## 🔮 Roadmap Futuro

- [ ] Integração com APIs reais de exchanges
- [ ] Dashboard web em React
- [ ] Machine Learning para otimização de parâmetros
- [ ] Suporte a múltiplos mercados (ações, cripto, forex)
- [ ] Estratégias adicionais (mean reversion, arbitragem)
- [ ] Alertas em tempo real (Telegram, Discord)
- [ ] Análise de sentimento do mercado
- [ ] Otimização de parâmetros automática

## 📝 Licença

MIT License - Veja [LICENSE](LICENSE) para detalhes

## 👨‍💻 Autor

Desenvolvido como projeto educacional em Python.

---

**Última atualização**: Fevereiro de 2026
