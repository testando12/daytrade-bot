# Planos Futuros — Daytrade Bot

> Anotado em: 10/03/2026

---

## Ativos e Mercados

### Prioridade 1 — Grátis, sem esforço
- [ ] **Alpha Vantage API** (chave grátis em alphavantage.co) — dados mais confiáveis para US stocks e Forex intraday real

### Prioridade 2 — Requer integração nova
- [ ] **CoinGecko API** (grátis em coingecko.com) — filtro fundamentalista antes de entrar em crypto (market cap, volume 24h, fear & greed index)

### Prioridade 3 — Requer conta Binance com futuros
- [ ] **Binance Futures** — habilitar short genuine + funding rate arbitrage

---

## Estratégias Novas

### Grid Stablecoin (baixo risco, renda constante)
- USDT/USDC oscila entre 0.998 e 1.002
- Grid engine já existe no bot — só apontar para esse par
- Risco próximo de zero, retorno estimado 1-3%/mês
- **Esforço: baixo** — só configurar par no grid existente

### Funding Rate Arbitrage (renda passiva, risco baixo)
- Long BTC spot + Short BTC perp = neutro em preço + coleta funding
- BTC funding atual ~0.01%/8h = ~0.97%/mês garantido
- **Esforço: médio** — integração com Binance Futures API
- **Necessidade:** conta Binance com futuros habilitado

### Arbitragem Cross-Exchange (sem risco de mercado)
- Compra em Binance, vende no Kraken/Coinbase quando há diferença de preço
- Diferenças de 0.05-0.3% aparecem dezenas de vezes por dia
- Potencial: 1-5%/mês
- **Esforço: médio** — API Kraken ou Coinbase (gratuitas)
- **Necessidade:** saldo pré-alocado nos dois lados

### Staking Automatizado (rendimento passivo)
- Bot move crypto para stake quando não está operando, retira quando há sinal
- SOL staking: ~7% a.a. | ETH (Lido): ~4% a.a. | ADA: ~4-5% a.a.
- **Esforço: médio** — integração com Lido SDK e delegação nativa SOL/ADA

### Mercados de Previsão — Polymarket
- Apostar em eventos (eleições, preços, etc.) quando odds estão erradas
- API gratuita, baseada em Polygon blockchain
- **Esforço: alto** — requer modelo de previsão + integração Polymarket API

### DEX Sniping (alto risco / alto retorno)
- Entrar em novos tokens nos primeiros 30-120 segundos do lançamento
- Potencial: 2x-50x, mas 90% dos lançamentos são scam
- **Esforço: muito alto** — node Ethereum/Solana próprio + filtro anti-rug
- **Risco: muito alto** — só considerar com capital pequeno dedicado

---

## Resumo de Priorização

| # | Ideia | Potencial/mês | Risco | Esforço |
|---|---|---|---|---|
| 1 | Grid Stablecoin | 1-3% | quase zero | baixo |
| 2 | Alpha Vantage key | dados melhores | zero | 5 min |
| 3 | Funding Rate Arb | 0.5-2% | baixo | médio |
| 4 | Arbitragem cross-exchange | 1-5% | baixo | médio |
| 5 | CoinGecko filtro | win rate melhor | zero | baixo |
| 6 | Staking automático | 4-7% a.a. | baixo | médio |
| 7 | Polymarket | variável | médio | alto |
| 8 | DEX Sniping | 0-100x | muito alto | muito alto |
