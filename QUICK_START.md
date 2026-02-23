# Quick Reference - Day Trade Bot

## 🚀 Iniciar Rápido

### Windows
```bash
# 1. Criar ambiente
python -m venv venv
venv\Scripts\activate

# 2. Instalar deps
pip install -r requirements.txt

# 3. Rodar servidor
python -m uvicorn app.main:app --reload --port 8000

# 4. Acessar API
# http://localhost:8000/docs
```

### Linux/Mac
```bash
# 1. Criar ambiente
python3 -m venv venv
source venv/bin/activate

# 2. Instalar deps
pip install -r requirements.txt

# 3. Rodar servidor
python -m uvicorn app.main:app --reload --port 8000
```

## 📊 Endpoints Principais

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/` | GET | Status do aplicativo |
| `/health` | GET | Health check |
| `/analyze/momentum` | POST | Analisa momentum dos ativos |
| `/analyze/risk` | POST | Analisa risco global (IRQ) |
| `/analyze/full` | POST | Análise completa + alocação |
| `/status` | GET | Status do bot |
| `/config` | GET | Configurações |

## 🧪 Testes

```bash
# Testar engines localmente (sem API)
python test_engines.py

# Executar backtesting
python backtest.py

# Inicializar banco de dados
python init_db.py

# Resetar banco
python init_db.py reset
```

## 🎯 Componentes Principais

### 1. **MomentumAnalyzer** (`app/engines/momentum.py`)
Calcula Momentum Score baseado em:
- Retorno percentual (50%)
- Tendência de médias móveis (30%)
- Força de volume (20%)

```python
from app.engines import MomentumAnalyzer

result = MomentumAnalyzer.calculate_momentum_score(prices, volumes)
print(result['momentum_score'])  # -1 a 1
print(result['classification'])  # FORTE_ALTA, ALTA_LEVE, LATERAL, QUEDA
```

### 2. **RiskAnalyzer** (`app/engines/risk.py`)
Detecta sinais de queda e calcula IRQ:
- S1: Perda de tendência
- S2: Pressão vendedora
- S3: Volatilidade
- S4: Divergência RSI
- S5: Sequência de quedas

```python
from app.engines import RiskAnalyzer

risk_result = RiskAnalyzer.calculate_irq(prices, volumes)
print(risk_result['irq_score'])  # 0 a 1
print(risk_result['s1_trend_loss'])
```

### 3. **PortfolioManager** (`app/engines/portfolio.py`)
Aloca capital e rebalanceia:
- Distribui capital baseado em momentum
- Aplica limites de proteção
- Rebalanceia automaticamente

```python
from app.engines import PortfolioManager

allocation = PortfolioManager.calculate_portfolio_allocation(
    momentum_scores, irq_score, total_capital
)
print(allocation)  # {BTC: 60, ETH: 45, ...}
```

## 📈 Fluxo de Dados

```
Market Data (Preços + Volumes)
    ↓
MomentumAnalyzer → Momentum Scores
    ↓
RiskAnalyzer → IRQ Score
    ↓
PortfolioManager → Alocação + Rebalanceamento
    ↓
Trading Rules → Ordens (BUY/SELL/HOLD)
    ↓
Database + Dashboard
```

## ⚙️ Configurações Importantes

Editar em `app/core/config.py` ou `.env`:

```python
INITIAL_CAPITAL = 150.0              # Capital inicial
MAX_POSITION_PERCENTAGE = 0.30       # 30% máximo por ativo
MIN_POSITION_AMOUNT = 10.0           # Mínimo R$10
STOP_LOSS_PERCENTAGE = 0.05          # 5% stop loss
REBALANCE_INTERVAL = 300             # A cada 5 minutos

# Proteção de Risco
IRQ_THRESHOLD_HIGH = 0.70            # Começa proteção
IRQ_THRESHOLD_VERY_HIGH = 0.80       # Proteção forte
IRQ_THRESHOLD_CRITICAL = 0.90        # Sair do mercado

# Pesos de Momentum
MOMENTUM_WEIGHT_RETURN = 0.50
MOMENTUM_WEIGHT_TREND = 0.30
MOMENTUM_WEIGHT_VOLUME = 0.20
```

## 🔍 Estrutura de Pastas

```
daytrade_bot/
├── app/
│   ├── main.py              ← API FastAPI
│   ├── core/
│   │   ├── config.py        ← Configurações
│   │   └── database.py      ← DB Connection
│   ├── engines/
│   │   ├── momentum.py      ← Momentum Score
│   │   ├── risk.py          ← IRQ Score
│   │   └── portfolio.py     ← Alocação
│   ├── models/
│   │   └── database.py      ← SQLAlchemy Models
│   └── schemas/
│       └── schemas.py       ← Pydantic Schemas
├── test_engines.py          ← Testes
├── backtest.py              ← Backtesting
├── init_db.py               ← Init Database
├── requirements.txt         ← Dependências
└── README.md                ← Documentação
```

## 📊 Regras Automáticas

```
FORTE_ALTA (Momentum > 0.5)      → Aumentar 20%
ALTA_LEVE (0.15 < M < 0.5)       → Manter
LATERAL (-0.15 < M < 0.15)       → Reduzir ao mínimo
QUEDA (M < -0.15)                → Reduzir 50%
```

## 🛡️ Proteção de Risco

```
IRQ ≤ 0.70 →  Operação normal
0.70 < IRQ ≤ 0.80 → Reduzir 40%
0.80 < IRQ ≤ 0.90 → Reduzir 70%
IRQ > 0.90 → Sair 100%
```

## 💡 Dicas

1. Sempre teste em **papertrading** primeiro
2. Comece com capital pequeno (R$50-150)
3. Monitore o bot regularmente
4. Ajuste os pesos se necessário
5. Mantenha stop losses ativados
6. Diversifique entre ativos
7. Não confie cegamente - sempre revise

## 🐛 Debug

```bash
# Ver logs em tempo real
python -m uvicorn app.main:app --reload --log-level debug

# Testar endpoint específico
curl http://localhost:8000/analyze/full

# Verificar configurações
curl http://localhost:8000/config
```

## 📞 Suporte

Para problemas:
1. Verifique o `.env`
2. Veja os logs da API
3. Execute `test_engines.py` para debug
4. Rode `backtest.py` para validar estratégia

---

**Versão**: 1.0.0
**Última atualização**: Fevereiro 2026
