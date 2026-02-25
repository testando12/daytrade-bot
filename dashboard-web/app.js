/* =============================================
   DAY TRADE BOT — App JavaScript
   SPA com roteamento, fetch API e Chart.js
   ============================================= */

'use strict';

// =============================================
// CONFIG
// =============================================

// Auto-detecta: localhost em dev, mesma origem no Railway/produção
let API_BASE = (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
  ? 'http://localhost:8001'
  : window.location.origin;
let currentPage = 'dashboard';
let autoRefreshInterval = null;
let charts = {};

// =============================================
// INIT
// =============================================

document.addEventListener('DOMContentLoaded', () => {
  checkApiConnection();
  loadPage('dashboard');
  // Auto-refresh do dashboard a cada 30s
  autoRefreshInterval = setInterval(() => {
    if (currentPage === 'dashboard') loadDashboard();
  }, 30000);
});

// =============================================
// NAVIGAÇÃO / ROTEAMENTO
// =============================================

const PAGE_NAMES = {
  dashboard: 'Dashboard',
  live: 'Análise ao Vivo',
  market: 'Mercado',
  trade: 'Trade',
  portfolio: 'Portfólio',
  risk: 'Gestão de Risco',
  history: 'Histórico',
  indicators: 'Indicadores Técnicos',
  finance: 'Calculadora Financeira',
  events: 'Eventos & Dividendos',
  security: 'Segurança & Compliance',
  settings: 'Configurações',
};

function navigate(page) {
  // Remove active class de todos nav-items
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  // Adiciona active no item correto
  const activeItem = document.querySelector(`[data-page="${page}"]`);
  if (activeItem) activeItem.classList.add('active');

  // Esconde todas as páginas
  document.querySelectorAll('.page').forEach(el => el.classList.remove('active'));

  // Mostra a página selecionada
  const pageEl = document.getElementById(`page-${page}`);
  if (pageEl) pageEl.classList.add('active');

  // Atualiza breadcrumb
  document.getElementById('topbar-page-name').textContent = PAGE_NAMES[page] || page;

  currentPage = page;
  loadPage(page);
}

function loadPage(page) {
  switch (page) {
    case 'dashboard': loadDashboard(); break;
    case 'live':      loadLiveAnalysis(); break;
    case 'market':    loadMarketPage(); break;
    case 'trade':     loadTradePage(); break;
    case 'portfolio': loadPortfolio(); break;
    case 'risk':      loadRiskPage(); break;
    case 'history':   loadHistoryPage(); break;
    case 'indicators': loadIndicators(); break;
    case 'finance':   loadFinance(); break;
    case 'events':    loadEvents(); break;
    case 'security':  loadSecurity(); break;
    case 'settings':  loadSettings(); break;
  }
}

function refreshCurrentPage() {
  loadPage(currentPage);
  toast('Dados atualizados', 'success');
}

// =============================================
// SIDEBAR TOGGLE
// =============================================

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const main = document.getElementById('main-content');
  sidebar.classList.toggle('collapsed');
  main.classList.toggle('sidebar-collapsed');
}

// =============================================
// API HELPER
// =============================================

async function api(path, options = {}, timeoutMs = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      ...options,
    });
    if (!res.ok) throw new Error(`API ${path} retornou ${res.status}`);
    return res.json();
  } catch (e) {
    if (e.name === 'AbortError') throw new Error(`Timeout na requisição ${path} (${timeoutMs/1000}s)`);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

async function checkApiConnection() {
  try {
    await api('/health');
    setApiStatus(true);
  } catch (e) {
    setApiStatus(false);
  }
}

function setApiStatus(online) {
  const dot = document.getElementById('api-dot');
  const label = document.getElementById('api-label');
  const sdot = document.getElementById('sidebar-status-dot');
  const slabel = document.getElementById('sidebar-api-label');

  if (online) {
    dot.className = 'dot';
    label.textContent = 'API Online';
    sdot.className = 'status-dot';
    slabel.textContent = 'API Online';
  } else {
    dot.className = 'dot red';
    label.textContent = 'API Offline';
    sdot.className = 'status-dot offline';
    slabel.textContent = 'API Offline';
  }
}

function applyApiUrl() {
  const val = document.getElementById('api-url-input').value.trim().replace(/\/$/, '');
  if (val) {
    API_BASE = val;
    document.getElementById('swagger-link').href = `${API_BASE}/docs`;
    checkApiConnection();
    toast(`API configurada para ${API_BASE}`, 'info');
  }
}

function setLastUpdate() {
  const pill = document.getElementById('last-update-pill');
  const time = document.getElementById('last-update-time');
  pill.style.display = 'flex';
  const now = new Date();
  time.textContent = now.toLocaleTimeString('pt-BR');
}

// =============================================
// TOAST
// =============================================

function toast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const icons = { success: '✅', error: '❌', info: 'ℹ️', warning: 'âš ️' };
  const div = document.createElement('div');
  div.className = `toast ${type}`;
  div.innerHTML = `<span>${icons[type] || 'ℹ️'}</span><span>${message}</span>`;
  container.appendChild(div);
  setTimeout(() => div.remove(), 3500);
}

// =============================================
// HELPERS / FORMATTERS
// =============================================

function fmtMoney(val) {
  if (val == null) return 'R$ --';
  return `R$ ${Number(val).toFixed(2).replace('.', ',')}`;
}

function fmtPct(val) {
  if (val == null) return '--%';
  return `${(Number(val) * 100).toFixed(1)}%`;
}

function fmtScore(val) {
  if (val == null) return '--';
  return Number(val).toFixed(4);
}

function fmtPrice(val) {
  if (val == null) return '--';
  const n = Number(val);
  if (n > 100) return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return `$${n.toFixed(4)}`;
}

function classifBadge(cls) {
  const map = {
    'FORTE_ALTA': 'badge-green',
    'ALTA_LEVE':  'badge-blue',
    'LATERAL':    'badge-gray',
    'QUEDA':      'badge-red',
  };
  return `<span class="badge ${map[cls] || 'badge-gray'}">${cls || '--'}</span>`;
}

function irqBadgeClass(level) {
  const map = {
    'NORMAL':      'badge-green',
    'ALTO':        'badge-yellow',
    'MUITO_ALTO':  'badge-orange',
    'CRÍTICO':     'badge-red',
    'CRITICO':     'badge-red',
  };
  return map[level] || 'badge-gray';
}

function actionBadge(action) {
  const map = {
    'BUY':    'badge-green',
    'SELL':   'badge-red',
    'HOLD':   'badge-gray',
    'COMPRA': 'badge-green',
    'VENDA':  'badge-red',
  };
  return `<span class="badge ${map[action] || 'badge-gray'}">${action}</span>`;
}

function scoreColor(score) {
  if (score >= 0.5) return 'var(--green)';
  if (score >= 0.2) return 'var(--blue)';
  if (score >= 0)   return 'var(--yellow)';
  return 'var(--red)';
}

// =============================================
// CHART HELPERS
// =============================================

Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#30363d';

function destroyChart(id) {
  if (charts[id]) {
    charts[id].destroy();
    delete charts[id];
  }
}

// Helper: verifica horário B3 no navegador (BRT = UTC-3, seg-sex 10-17h)
function _is_market_open_js() {
  const now = new Date();
  const brt = new Date(now.toLocaleString('en-US', { timeZone: 'America/Sao_Paulo' }));
  const h = brt.getHours(), dow = brt.getDay();
  return dow >= 1 && dow <= 5 && h >= 10 && h < 17;
}

// =============================================
// PAGE: DASHBOARD
// =============================================

async function loadDashboard() {
  try {
    setApiStatus(true);

    // 1) Fast data first (trade + risk load in ~2-5s)
    const [tradeStatus, riskStatus, perfData] = await Promise.all([
      api('/trade/status').catch(() => null),
      api('/risk/status').catch(() => null),
      api('/performance').catch(() => null),
    ]);

    // Render partial KPIs immediately from fast endpoints
    renderDashboardKPIsPartial(tradeStatus, riskStatus, perfData);

    // Prices separately
    loadMarketPrices();
    setLastUpdate();

    // 2) Heavy analysis in background (90s timeout)
    const analysis = await api('/analyze/full', { method: 'POST' }, 90000).catch(() => null);
    if (analysis && analysis.success) {
      renderDashboardKPIs(analysis.data, riskStatus, tradeStatus);
      renderMomentumChart(analysis.data);
      renderRiskRadar(analysis.data);
      renderDashboardAllocation(analysis.data);
    }
    setLastUpdate();
  } catch (e) {
    setApiStatus(false);
    toast('Erro ao conectar com a API', 'error');
  }
}

function renderDashboardKPIsPartial(tradeStatus, riskStatus, perfData) {
  // Render KPIs from fast endpoints immediately
  const td = tradeStatus?.data || {};
  const perf = perfData?.data || {};
  const capital = td.capital_efetivo || td.capital || 2000;
  document.getElementById('kpi-capital').textContent = fmtMoney(capital);
  const pnlVal = perf.total_pnl || td.total_pnl || 0;
  const pnlEl = document.getElementById('kpi-pnl');
  if (pnlEl) {
    pnlEl.textContent = `P&L: ${fmtMoney(pnlVal)}`;
    pnlEl.className = `kpi-change ${pnlVal >= 0 ? 'up' : 'down'}`;
  }
  // Bot status from risk
  const canTrade = riskStatus?.data?.is_locked === false;
  const statusEl = document.getElementById('kpi-bot-status');
  if (statusEl) statusEl.textContent = canTrade ? 'Operacional' : 'Bloqueado';
  const tradeOkEl = document.getElementById('kpi-trade-ok');
  if (tradeOkEl) {
    tradeOkEl.textContent = canTrade ? 'Permitido operar' : (riskStatus?.data?.lock_reason || '');
    tradeOkEl.className = `kpi-change ${canTrade ? 'up' : 'down'}`;
  }
}

function renderDashboardKPIs(data, riskStatus, tradeStatus) {
  // Capital — usa capital_efetivo (capital base + pnl hoje)
  const capital = tradeStatus?.data?.capital_efetivo || tradeStatus?.data?.capital || data.market_data?.capital || data.portfolio?.capital || 2000;
  document.getElementById('kpi-capital').textContent = fmtMoney(capital);
  const pnlVal = tradeStatus?.data?.total_pnl || 0;
  const pnlEl = document.getElementById('kpi-pnl');
  if (pnlEl) {
    pnlEl.textContent = `P&L: ${fmtMoney(pnlVal)}`;
    pnlEl.className = `kpi-change ${pnlVal >= 0 ? 'up' : 'down'}`;
  }

  // IRQ
  const irq = data.risk_analysis;
  if (irq) {
    const pct = (irq.irq_score * 100).toFixed(1);
    document.getElementById('kpi-irq').textContent = `${pct}%`;
    document.getElementById('kpi-irq-level').textContent = irq.level || irq.protection_level;
    document.getElementById('kpi-irq-level').className = `kpi-change ${irq.irq_score < 0.6 ? 'up' : 'down'}`;
    const badge = document.getElementById('irq-badge-dash');
    badge.textContent = `${irq.color} ${irq.level}`;
    badge.className = `badge ${irqBadgeClass(irq.level)}`;
  }

  // Best asset
  const mom = data.momentum_analysis;
  if (mom) {
    let best = null, bestScore = -999;
    for (const [asset, d] of Object.entries(mom)) {
      if (d.momentum_score > bestScore) { bestScore = d.momentum_score; best = asset; }
    }
    document.getElementById('kpi-best-asset').textContent = best || '--';
    document.getElementById('kpi-best-score').textContent = `Score: ${bestScore.toFixed(4)}`;
    document.getElementById('kpi-best-score').className = `kpi-change ${bestScore >= 0 ? 'up' : 'down'}`;
  }

  // Bot status
  const canTrade = riskStatus?.data?.is_locked === false;
  document.getElementById('kpi-bot-status').textContent = canTrade ? '✅ Operacional' : '🔒 Bloqueado';
  document.getElementById('kpi-trade-ok').textContent = canTrade ? 'Permitido operar' : (riskStatus?.data?.lock_reason || '');
  document.getElementById('kpi-trade-ok').className = `kpi-change ${canTrade ? 'up' : 'down'}`;
}

async function loadMarketPrices() {
  const el = document.getElementById('dash-prices-table');
  if (!el) return;

  try {
    const data = await api('/market/prices');
    if (!data.success) throw new Error();

    const prices = data.data;
    const keys = Object.keys(prices).slice(0, 8);
    let html = '<table><thead><tr><th>Ativo</th><th>Preço</th></tr></thead><tbody>';
    for (const asset of keys) {
      html += `<tr>
        <td><strong>${asset}</strong></td>
        <td>${fmtPrice(prices[asset])}</td>
      </tr>`;
    }
    html += '</tbody></table>';
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<div class="empty-state"><div class="empty-icon">📡</div><p>Binance offline ou sem conexão</p></div>`;
  }
}

function renderMomentumChart(data) {
  destroyChart('momentum');
  const mom = data.momentum_analysis;
  if (!mom) return;

  const labels = Object.keys(mom);
  const scores = Object.values(mom).map(v => v.momentum_score);
  const colors = scores.map(s => s >= 0.5 ? '#3fb950' : s >= 0.2 ? '#388bfd' : s >= 0 ? '#d29922' : '#f85149');

  const ctx = document.getElementById('chart-momentum');
  if (!ctx) return;

  charts['momentum'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Momentum Score',
        data: scores,
        backgroundColor: colors,
        borderRadius: 6,
        borderSkipped: false,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: {
          grid: { color: '#21262d' },
          ticks: { color: '#8b949e' },
        },
        x: {
          grid: { display: false },
          ticks: { color: '#8b949e' },
        }
      }
    }
  });

  const timeEl = document.getElementById('dashboard-momentum-time');
  if (timeEl) timeEl.textContent = new Date().toLocaleTimeString('pt-BR');
}

function renderRiskRadar(data) {
  destroyChart('risk-radar');
  const risk = data.risk_analysis;
  if (!risk) return;

  const ctx = document.getElementById('chart-risk-radar');
  if (!ctx) return;

  const signals = risk.signal_scores || {
    S1: risk.s1_trend_loss || 0,
    S2: risk.s2_selling_pressure || 0,
    S3: risk.s3_volatility || 0,
    S4: risk.s4_rsi_divergence || 0,
    S5: risk.s5_losing_streak || 0,
  };

  const labels = ['S1 Tendência', 'S2 Pressão', 'S3 Volatilidade', 'S4 RSI', 'S5 Perdas'];
  const vals = [signals.S1, signals.S2, signals.S3, signals.S4, signals.S5];

  charts['risk-radar'] = new Chart(ctx, {
    type: 'radar',
    data: {
      labels,
      datasets: [{
        label: 'Sinais IRQ',
        data: vals,
        backgroundColor: 'rgba(248, 81, 73, 0.15)',
        borderColor: '#f85149',
        pointBackgroundColor: '#f85149',
        pointRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        r: {
          min: 0,
          max: 1,
          grid: { color: '#30363d' },
          ticks: { display: false },
          pointLabels: { color: '#8b949e', font: { size: 11 } },
        }
      }
    }
  });
}

function renderDashboardAllocation(data) {
  const el = document.getElementById('dash-allocation');
  const allocs = data.allocations;
  if (!allocs) {
    el.innerHTML = '<div class="empty-state"><p>Sem dados de alocação</p></div>';
    return;
  }

  let total = 0;
  let html = '';
  const entries = Object.entries(allocs).filter(([, v]) => v.recommended_amount > 0);

  for (const [, d] of entries) total += d.recommended_amount;

  for (const [asset, d] of entries) {
    const pct = total > 0 ? (d.recommended_amount / (total * 1.5)) * 100 : 0;
    html += `
      <div style="margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
          <span><strong>${asset}</strong></span>
          <span>${fmtMoney(d.recommended_amount)}</span>
        </div>
        <div class="progress-bar">
          <div class="progress-fill ${d.recommended_amount > 0 ? 'blue' : 'gray'}" style="width:${Math.min(pct, 100)}%"></div>
        </div>
      </div>`;
  }

  if (!html) html = '<div class="empty-state"><p>Sem alocações recomendadas no momento</p></div>';
  el.innerHTML = html;

  const totalEl = document.getElementById('allocation-total');
  if (totalEl) totalEl.textContent = fmtMoney(total);
}

// =============================================
// PAGE: ANÁLISE AO VIVO
// =============================================

async function loadLiveAnalysis() {
  const el = document.getElementById('live-analysis-result');
  const timeEl = document.getElementById('live-analysis-time');
  el.innerHTML = '<div class="loading-overlay"><div class="spinner"></div> Buscando dados da Binance...</div>';

  try {
    const data = await api('/market/analyze-live', { method: 'POST' }, 90000);
    if (data.success) {
      renderAnalysisResult(el, data.data, '⚡ Binance Live');
      timeEl.textContent = `Atualizado: ${new Date().toLocaleTimeString('pt-BR')}`;
      toast('Análise ao vivo concluída', 'success');
    } else {
      el.innerHTML = `<div class="empty-state"><div class="empty-icon">❌</div><p>${data.detail || 'Erro na análise'}</p></div>`;
    }
  } catch (e) {
    el.innerHTML = `<div class="empty-state"><div class="empty-icon">📡</div><p>Binance offline — tente a análise com dados de teste</p></div>`;
    toast('Binance indisponível', 'warning');
  }
}

async function loadFullAnalysis() {
  const el = document.getElementById('live-analysis-result');
  const timeEl = document.getElementById('live-analysis-time');
  el.innerHTML = '<div class="loading-overlay"><div class="spinner"></div> Analisando...</div>';

  try {
    const data = await api('/analyze/full', { method: 'POST' }, 90000);
    if (data.success) {
      renderAnalysisResult(el, data.data, '🧪 Dados de Teste');
      timeEl.textContent = `Atualizado: ${new Date().toLocaleTimeString('pt-BR')}`;
    }
  } catch (e) {
    el.innerHTML = `<div class="empty-state"><div class="empty-icon">❌</div><p>Erro na API</p></div>`;
  }
}

function renderAnalysisResult(el, data, source) {
  const mom = data.momentum_analysis || {};
  const risk = data.risk_analysis || {};
  const allocs = data.allocations || {};

  const irqPct = (risk.irq_score * 100).toFixed(1);
  const irqColor = risk.irq_score < 0.6 ? 'green' : risk.irq_score < 0.8 ? 'yellow' : 'red';

  let assetsHtml = '';
  for (const [asset, m] of Object.entries(mom)) {
    const alloc = allocs[asset];
    const score = m.momentum_score;
    const cls = m.classification;
    assetsHtml += `
      <div class="card" style="padding:16px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <strong style="font-size:16px">${asset}</strong>
          ${classifBadge(cls)}
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px">
          <div><span class="text-muted">Score:</span> <strong style="color:${scoreColor(score)}">${score.toFixed(4)}</strong></div>
          <div><span class="text-muted">Retorno:</span> <strong class="${m.return_pct >= 0 ? 'text-green' : 'text-red'}">${(m.return_pct * 100).toFixed(2)}%</strong></div>
          <div><span class="text-muted">Trend:</span> ${m.trend_status}</div>
          <div><span class="text-muted">Alocação:</span> <strong>${alloc ? fmtMoney(alloc.recommended_amount) : '--'}</strong></div>
        </div>
      </div>`;
  }

  el.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <span class="badge badge-blue">${source}</span>
      <span style="font-size:12px;color:var(--text-muted)">${data.timestamp ? new Date(data.timestamp).toLocaleString('pt-BR') : ''}</span>
    </div>

    <div class="card mb-16" style="padding:16px">
      <div class="card-title mb-12">🛡️ IRQ — Índice de Risco de Queda</div>
      <div style="display:flex;align-items:center;gap:24px">
        <div>
          <div style="font-size:48px;font-weight:800;color:var(--${irqColor})">${irqPct}%</div>
          <div class="text-muted" style="font-size:12px">Nível de Risco: <strong class="text-${irqColor}">${risk.level || risk.protection_level}</strong></div>
        </div>
        <div style="flex:1">
          ${renderSignalsInline(risk)}
        </div>
        <div style="text-align:right">
          <div style="font-size:12px;color:var(--text-muted)">RSI</div>
          <div style="font-size:22px;font-weight:700">${risk.rsi ? risk.rsi.toFixed(1) : '--'}</div>
          <div style="font-size:12px;color:var(--text-muted)">Redução Capital</div>
          <div style="font-size:18px;font-weight:700;color:var(--red)">${risk.reduction_percentage != null ? (risk.reduction_percentage * 100).toFixed(0) + '%' : '0%'}</div>
        </div>
      </div>
    </div>

    <div style="font-size:13px;font-weight:600;color:var(--text-secondary);margin-bottom:12px;text-transform:uppercase;letter-spacing:0.5px">
      Análise por Ativo (${Object.keys(mom).length})
    </div>
    <div class="assets-grid">
      ${assetsHtml}
    </div>`;
}

function renderSignalsInline(risk) {
  const signals = risk.signal_scores || {
    S1: risk.s1_trend_loss || 0,
    S2: risk.s2_selling_pressure || 0,
    S3: risk.s3_volatility || 0,
    S4: risk.s4_rsi_divergence || 0,
    S5: risk.s5_losing_streak || 0,
  };
  const labels = { S1: 'Tendência', S2: 'Pressão Vend.', S3: 'Volatilidade', S4: 'RSI Div.', S5: 'Seq. Perdas' };
  let html = '';
  for (const [k, v] of Object.entries(signals)) {
    const pct = Math.min((v || 0) * 100, 100);
    const color = pct > 70 ? 'red' : pct > 40 ? 'yellow' : 'green';
    html += `
      <div class="signal-row">
        <div class="signal-label">${k} ${labels[k] || ''}</div>
        <div class="signal-bar-wrap">
          <div class="progress-bar"><div class="progress-fill ${color}" style="width:${pct}%"></div></div>
        </div>
        <div class="signal-value text-${color}">${(v || 0).toFixed(3)}</div>
      </div>`;
  }
  return html;
}

// =============================================
// PAGE: MERCADO
// =============================================

async function loadMarketPage() {
  const grid      = document.getElementById('market-assets-grid');
  const tbody     = document.getElementById('market-table-body');
  const ptbody    = document.getElementById('market-predict-tbody');
  const scoreCard = document.getElementById('market-score-cards');
  const scoreTb   = document.getElementById('market-score-tbody');
  const timeEl    = document.getElementById('market-update-time');

  grid.innerHTML      = '<div class="loading-overlay" style="grid-column:1/-1"><div class="spinner"></div> Carregando...</div>';
  tbody.innerHTML     = '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--text-muted)">Carregando...</td></tr>';
  ptbody.innerHTML    = '<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--text-muted)">Calculando projecoes...</td></tr>';
  if (scoreCard) scoreCard.innerHTML = '<div class="loading-overlay" style="grid-column:1/-1"><div class="spinner"></div> Calculando scores...</div>';

  try {
    const [pricesRes, analysisRes, predictRes, scoreRes] = await Promise.all([
      api('/market/prices', {}, 60000).catch(() => null),
      api('/analyze/full', { method: 'POST' }, 90000).catch(() => null),
      api('/market/predict', {}, 60000).catch(() => null),
      api('/market/score', {}, 60000).catch(() => null),
    ]);

    const prices    = pricesRes?.data   || {};
    const mom       = analysisRes?.data?.momentum_analysis || {};
    const preds     = predictRes?.data  || {};
    const scoreData = scoreRes?.data    || {};
    const top3      = scoreRes?.top3    || [];

    // Cards de ativos (agora com mini-projecao de 1h)
    // Usa preços se disponíveis; caso contrário, usa ativos do momentum
    const cardAssets = Object.keys(prices).length
      ? Object.keys(prices)
      : Object.keys(mom);
    let cardsHtml = '';
    for (const asset of cardAssets) {
      const price = prices[asset];
      const m     = mom[asset];
      const p     = preds[asset];
      const score = m?.momentum_score;
      const cls   = m?.classification || 'LATERAL';
      const up    = score != null && score >= 0;
      const c1h   = p?.change_1h_pct;
      const col1h = c1h != null ? (c1h >= 0 ? 'var(--green)' : 'var(--red)') : '';
      cardsHtml += `
        <div class="asset-card">
          <div class="asset-header">
            <span class="asset-symbol">${asset}</span>
            ${classifBadge(cls)}
          </div>
          <div class="asset-price">${price != null ? fmtPrice(price) : '--'}</div>
          ${score != null ? `<div class="asset-change ${up ? 'up' : 'down'}">${up ? '&#9650;' : '&#9660;'} Score: ${score.toFixed(4)}</div>` : ''}
          ${c1h != null ? `<div style="font-size:11px;color:${col1h};margin-top:4px">${c1h >= 0 ? '&#9650;' : '&#9660;'} 1h: ${fmtPrice(p.pred_1h)} (${c1h > 0 ? '+' : ''}${c1h}%)</div>` : ''}
        </div>`;
    }
    grid.innerHTML = cardsHtml || '<div class="empty-state" style="grid-column:1/-1"><p>Sem dados</p></div>';

    // Tabela de precos
    let rows = '';
    const assets = Object.keys(prices).length ? Object.keys(prices) : Object.keys(mom);
    for (const asset of assets) {
      const price = prices[asset];
      const m = mom[asset];
      rows += `<tr>
        <td><strong>${asset}</strong></td>
        <td>${price ? fmtPrice(price) : '--'}</td>
        <td><span style="color:${scoreColor(m?.momentum_score || 0)}">${m ? m.momentum_score.toFixed(4) : '--'}</span></td>
        <td>${m ? classifBadge(m.classification) : '--'}</td>
        <td class="${m?.return_pct >= 0 ? 'text-green' : 'text-red'}">${m ? (m.return_pct * 100).toFixed(2) + '%' : '--'}</td>
        <td>${m?.trend_status || '--'}</td>
      </tr>`;
    }
    tbody.innerHTML = rows || '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--text-muted)">Sem dados</td></tr>';

    // Tabela de projecoes matematicas
    const confColor = c => c === 'Alta' ? 'var(--green)' : c === 'Media' ? 'var(--yellow)' : 'var(--red)';
    const sign      = v => v > 0 ? '+' : '';
    let prows = '';
    const predEntries = Object.entries(preds)
      .sort((a, b) => Math.abs(b[1].change_1h_pct) - Math.abs(a[1].change_1h_pct));
    for (const [asset, p] of predEntries) {
      const c1h = p.change_1h_pct;
      const c1d = p.change_1d_pct;
      prows += `<tr>
        <td><strong>${asset}</strong></td>
        <td>${fmtPrice(p.current)}</td>
        <td style="font-weight:700;color:${c1h >= 0 ? 'var(--green)' : 'var(--red)'}">
          ${fmtPrice(p.pred_1h)}
          <div style="font-size:10px;color:var(--text-muted)">banda: ${fmtPrice(p.conf_low_1h)} &ndash; ${fmtPrice(p.conf_high_1h)}</div>
        </td>
        <td class="${c1h >= 0 ? 'text-green' : 'text-red'}" style="font-weight:700">${sign(c1h)}${c1h}%</td>
        <td style="font-weight:700;color:${c1d >= 0 ? 'var(--green)' : 'var(--red)'}">
          ${p.pred_1d ? fmtPrice(p.pred_1d) : '--'}
          ${p.pred_1d ? `<div style="font-size:10px;color:var(--text-muted)">banda: ${fmtPrice(p.conf_low_1d)} &ndash; ${fmtPrice(p.conf_high_1d)}</div>` : ''}
        </td>
        <td class="${c1d >= 0 ? 'text-green' : 'text-red'}" style="font-weight:700">${p.pred_1d ? sign(c1d) + c1d + '%' : '--'}</td>
        <td><span style="font-size:12px;font-weight:600;color:${confColor(p.confidence)}">${p.confidence}</span></td>
      </tr>`;
    }
    ptbody.innerHTML = prows || '<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--text-muted)">Sem dados de projecao</td></tr>';

    // ── Score de Oportunidade ──────────────────────────────────────────────
    const scoreCol  = s => s >= 70 ? 'var(--green)' : s >= 45 ? 'var(--yellow)' : 'var(--red)';
    const emaLabel  = sig => ({ 'ALTA_FORTE': '&#8679; Alta Forte', 'ALTA': '&#8679; Alta',
                                 'NEUTRO': '&#8594; Neutro', 'BAIXA': '&#8681; Baixa',
                                 'SEM_DADOS': '--' })[sig] || sig;
    const newsLbl   = n => n > 0.2 ? '&#128240;+' : n < -0.2 ? '&#128240;&#8722;' : '&#128240;~';

    if (scoreCard) {
      const medals = ['&#129351;','&#129352;','&#129353;'];
      const top3Html = top3.slice(0,3).map((asset, idx) => {
        const s = scoreData[asset]; if (!s) return '';
        const c1h = s.change_1h_pct, c1d = s.change_1d_pct;
        return `<div class="asset-card" style="border:2px solid ${scoreCol(s.score)};position:relative">
          <div style="position:absolute;top:8px;right:8px;font-size:18px">${medals[idx]}</div>
          <div class="asset-header">
            <span class="asset-symbol">${asset}</span>
            <span style="font-size:22px;font-weight:800;color:${scoreCol(s.score)}">${s.score}</span>
          </div>
          <div class="asset-price">${fmtPrice(s.current)}</div>
          <div style="font-size:11px;margin-top:6px;display:grid;grid-template-columns:1fr 1fr;gap:3px">
            <span>RSI: <b>${s.rsi}</b></span>
            <span>Vol: <b>${s.vol_ratio}&#215;</b></span>
            <span style="color:${c1h>=0?'var(--green)':'var(--red)'}">1h: ${c1h>0?'+':''}${c1h}%</span>
            <span style="color:${c1d>=0?'var(--green)':'var(--red)'}">1d: ${c1d>0?'+':''}${c1d}%</span>
          </div>
          <div style="font-size:10px;margin-top:5px;color:var(--text-muted)">${emaLabel(s.ema_signal)} &nbsp; ${newsLbl(s.news)}</div>
        </div>`;
      }).join('');
      scoreCard.innerHTML = top3Html || '<div class="empty-state" style="grid-column:1/-1"><p>Sem dados de score</p></div>';
    }

    if (scoreTb) {
      let srows = '', rank = 1;
      for (const [asset, s] of Object.entries(scoreData)) {
        const c1h = s.change_1h_pct, c1d = s.change_1d_pct;
        const isTop = top3.includes(asset);
        const medal = isTop ? ['&#129351;','&#129352;','&#129353;'][top3.indexOf(asset)] : rank;
        srows += `<tr style="${isTop?'background:rgba(0,200,83,0.06)':''}">
          <td>${medal}</td>
          <td><strong>${asset}</strong></td>
          <td>
            <span style="font-weight:800;font-size:15px;color:${scoreCol(s.score)}">${s.score}</span>
            <div style="font-size:9px;color:var(--text-muted)">RSI:${s.breakdown.rsi} EMA:${s.breakdown.ema} Vol:${s.breakdown.volume} Prev:${s.breakdown.prediction} News:${s.breakdown.news}</div>
          </td>
          <td style="color:${s.rsi<35?'var(--green)':s.rsi>70?'var(--red)':'var(--text)'}">${s.rsi}</td>
          <td>${emaLabel(s.ema_signal)}</td>
          <td>${s.vol_ratio}&#215;</td>
          <td class="${c1h>=0?'text-green':'text-red'}">${c1h>0?'+':''}${c1h}%</td>
          <td class="${c1d>=0?'text-green':'text-red'}">${c1d>0?'+':''}${c1d}%</td>
          <td>${s.news_found ? newsLbl(s.news) : '<span style="color:var(--text-muted)">sem dados</span>'}</td>
        </tr>`;
        rank++;
      }
      scoreTb.innerHTML = srows || '<tr><td colspan="9" style="text-align:center;padding:24px;color:var(--text-muted)">Sem dados de score</td></tr>';
    }

    timeEl.textContent = `Atualizado: ${new Date().toLocaleTimeString('pt-BR')}`;

  } catch (e) {
    grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="empty-icon">&#128225;</div><p>Erro ao carregar dados</p></div>';
    toast('Erro ao carregar mercado', 'error');
  }
}
// =============================================
// PAGE: PORTFÓLIO
// =============================================

async function loadPortfolio() {
  const summaryEl = document.getElementById('portfolio-summary');
  const tbodyEl = document.getElementById('portfolio-table-body');
  const totalEl = document.getElementById('portfolio-total');
  const timeEl = document.getElementById('portfolio-update-time');

  summaryEl.innerHTML = '<div class="loading-overlay"><div class="spinner"></div></div>';
  tbodyEl.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--text-muted)">Calculando...</td></tr>';

  try {
    // 1) Fast data first (loads in 2-5s)
    const [tradeStatus, perfData] = await Promise.all([
      api('/trade/status').catch(() => null),
      api('/performance').catch(() => null),
    ]);

    // Render partial data immediately
    const td = tradeStatus?.data || {};
    const perf = perfData?.data || {};
    const setEl = (id, val, positive) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = val;
      if (positive !== undefined) el.style.color = positive ? 'var(--green)' : 'var(--red)';
    };
    setEl('ptf-capital', fmtMoney(td.capital_efetivo || td.capital || 2000));
    setEl('ptf-pnl-today', fmtMoney(perf.pnl_today || td.pnl_hoje || 0), (perf.pnl_today || 0) >= 0);
    setEl('ptf-pnl-5m',   fmtMoney(perf.pnl_today_5m || 0), (perf.pnl_today_5m || 0) >= 0);
    setEl('ptf-pnl-1h',   fmtMoney(perf.pnl_today_1h || 0), (perf.pnl_today_1h || 0) >= 0);
    setEl('ptf-pnl-1d',   fmtMoney(perf.pnl_today_1d || 0), (perf.pnl_today_1d || 0) >= 0);
    setEl('ptf-pnl-total', fmtMoney(perf.total_pnl || td.total_pnl || 0), (perf.total_pnl || 0) >= 0);

    // Quick summary from trade data
    const capital = td.capital_efetivo || td.capital || 2000;
    const totalPnl = perf.total_pnl || td.total_pnl || 0;
    const tradePositions = td.positions || {};
    const posCount = Object.keys(tradePositions).length;
    summaryEl.innerHTML = `
      <div class="config-item"><span class="config-key">Capital do Bot</span><span class="config-value">${fmtMoney(capital)}</span></div>
      <div class="config-item"><span class="config-key">P&L Total</span><span class="config-value ${totalPnl >= 0 ? 'text-green' : 'text-red'}">${fmtMoney(totalPnl)}</span></div>
      <div class="config-item"><span class="config-key">Posições Ativas</span><span class="config-value">${posCount}</span></div>
      <div class="config-item"><span class="config-key">Ciclos Hoje</span><span class="config-value">${perf.today_cycles || 0}</span></div>
    `;

    // 2) Heavy analysis in background (90s timeout)
    const data = await api('/analyze/full', { method: 'POST' }, 90000).catch(() => null);
    if (data && data.success) {

    // Full data from analysis
    const allocs = data.data.allocations || {};
    const mom = data.data.momentum_analysis || {};
    const irq = data.data.risk_analysis?.irq_score || 0;

    // Usar posições reais do trade quando existirem
    const hasTradePos = Object.keys(tradePositions).length > 0;

    let total = 0;
    let entries;

    if (hasTradePos) {
      entries = Object.entries(tradePositions);
      for (const [, d] of entries) total += d.amount || 0;
    } else {
      entries = Object.entries(allocs);
      for (const [, d] of entries) total += d.recommended_amount || 0;
    }

    totalEl.textContent = `Total: ${fmtMoney(total)}`;

    // Pie chart
    destroyChart('portfolio-pie');
    const pieCtx = document.getElementById('chart-portfolio-pie');
    const pieEntries = hasTradePos
      ? entries.filter(([, d]) => (d.amount || 0) > 0)
      : entries.filter(([, d]) => (d.recommended_amount || 0) > 0);
    const pieColors = ['#388bfd','#3fb950','#d29922','#f85149','#db6d28','#8b949e','#a5d6ff','#7ee787','#c9d1d9','#f778ba','#56d4dd','#d2a8ff'];

    if (pieEntries.length > 0 && pieCtx) {
      charts['portfolio-pie'] = new Chart(pieCtx, {
        type: 'doughnut',
        data: {
          labels: pieEntries.map(([a]) => a),
          datasets: [{
            data: pieEntries.map(([, d]) => hasTradePos ? d.amount : d.recommended_amount),
            backgroundColor: pieColors.slice(0, pieEntries.length),
            borderWidth: 2,
            borderColor: '#1c2128',
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: '65%',
          plugins: {
            legend: {
              position: 'bottom',
              labels: { color: '#8b949e', padding: 12, font: { size: 11 } }
            }
          }
        }
      });
    } else if (pieCtx) {
      pieCtx.parentElement.innerHTML = '<div class="empty-state"><div class="empty-icon">💼</div><p>Sem posições — execute um ciclo no Trade</p></div>';
    }

    // Summary — update with full analysis data
    summaryEl.innerHTML = `
      <div class="config-item"><span class="config-key">Capital do Bot</span><span class="config-value">${fmtMoney(capital)}</span></div>
      <div class="config-item"><span class="config-key">Total Alocado</span><span class="config-value">${fmtMoney(total)}</span></div>
      <div class="config-item"><span class="config-key">P&L Total</span><span class="config-value ${totalPnl >= 0 ? 'text-green' : 'text-red'}">${fmtMoney(totalPnl)}</span></div>
      <div class="config-item"><span class="config-key">IRQ Score</span><span class="config-value text-${irq < 0.6 ? 'green' : 'red'}">${(irq*100).toFixed(1)}%</span></div>
      <div class="config-item"><span class="config-key">Posições Ativas</span><span class="config-value">${posCount}</span></div>
      <div class="config-item"><span class="config-key">Ciclos Hoje</span><span class="config-value">${perf.today_cycles || 0}</span></div>
    `;

    // Table — posições reais ou alocação teórica
    let rows = '';
    if (hasTradePos) {
      for (const [asset, d] of entries) {
        const m = mom[asset];
        const chg = d.change_pct || d.ret_pct || 0;
        rows += `<tr>
          <td><strong>${asset}</strong></td>
          <td>${m ? classifBadge(m.classification) : `<span class="badge badge-blue">${d.tf || '--'}</span>`}</td>
          <td>${fmtMoney(d.amount)}</td>
          <td>${d.pct ? d.pct.toFixed(1) + '%' : '--'}</td>
          <td class="${chg >= 0 ? 'text-green' : 'text-red'}">${chg.toFixed(3)}%</td>
          <td>${actionBadge(d.action || 'HOLD')}</td>
        </tr>`;
      }
    } else {
      for (const [asset, d] of entries) {
        const m = mom[asset];
        rows += `<tr>
          <td><strong>${asset}</strong></td>
          <td>${m ? classifBadge(m.classification) : '--'}</td>
          <td>${fmtMoney(d.current_amount)}</td>
          <td>${fmtMoney(d.recommended_amount)}</td>
          <td class="${d.change_percentage >= 0 ? 'text-green' : 'text-red'}">${d.change_percentage != null ? d.change_percentage.toFixed(1) + '%' : '--'}</td>
          <td>${actionBadge(d.action)}</td>
        </tr>`;
      }
    }
    tbodyEl.innerHTML = rows || '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--text-muted)">Sem posições — execute um ciclo no Trade</td></tr>';

    } // end if (data && data.success)

    timeEl.textContent = `Atualizado: ${new Date().toLocaleTimeString('pt-BR')}`;

  } catch (e) {
    summaryEl.innerHTML = '<div class="empty-state"><p>Erro ao carregar portfólio</p></div>';
    toast('Erro ao carregar portfólio', 'error');
  }
}

// =============================================
// PAGE: RISCO
// =============================================

async function loadRiskPage() {
  const timeEl = document.getElementById('risk-update-time');

  try {
    const [riskStatus, canTradeData, analysisData, tradeData] = await Promise.all([
      api('/risk/status'),
      api('/risk/can-trade'),
      api('/analyze/full', { method: 'POST' }, 90000).catch(() => null),
      api('/trade/status').catch(() => null),
    ]);

    const rs = riskStatus.data;
    const ct = canTradeData.data;
    const irq = analysisData?.data?.risk_analysis;
    const td = tradeData?.data || {};

    // KPIs - sincronizado com trade
    const canTrade = ct.allowed;
    const kpiCanEl = document.getElementById('risk-can-trade');
    kpiCanEl.textContent = canTrade ? '\u2705 SIM' : '\uD83D\uDD12 N\u00C3O';
    kpiCanEl.style.fontSize = '20px';
    document.getElementById('risk-can-trade-reason').textContent = ct.reason || '--';

    // P&L do trade real (synced) ou do risk_manager
    const tradePnl = rs.trade_total_pnl || td.total_pnl || 0;
    const dailyPnl = rs.daily_pnl || 0;
    const pnlEl = document.getElementById('risk-daily-pnl');
    pnlEl.textContent = fmtMoney(dailyPnl);
    pnlEl.className = `kpi-value ${dailyPnl >= 0 ? 'text-green' : 'text-red'}`;
    document.getElementById('risk-daily-pnl-label').textContent = `P&L Total: ${fmtMoney(tradePnl)} | M\u00E1x: ${fmtMoney(rs.daily_loss_limit?.max_loss)}`;

    document.getElementById('risk-trades-hour').textContent =
      `${rs.trade_limits?.trades_last_hour || 0} / ${ct.trade_limits?.remaining_hour != null ? (ct.trade_limits.remaining_hour + (rs.trade_limits?.trades_last_hour||0)) : 20}`;
    document.getElementById('risk-trades-day').textContent =
      `${rs.trade_limits?.trades_today || 0} / ${ct.trade_limits?.remaining_day != null ? (ct.trade_limits.remaining_day + (rs.trade_limits?.trades_today||0)) : 100}`;

    // Config items - com dados do trade
    const tradeCapital = rs.trade_capital || td.capital || 2000;
    const tradePos = rs.trade_positions_count || Object.keys(td.positions || {}).length;
    const configEl = document.getElementById('risk-config-items');
    configEl.innerHTML = `
      <div class="config-item"><span class="config-key">Capital Atual</span><span class="config-value">${fmtMoney(tradeCapital)}</span></div>
      <div class="config-item"><span class="config-key">P&L Total</span><span class="config-value ${tradePnl >= 0 ? 'text-green' : 'text-red'}">${fmtMoney(tradePnl)}</span></div>
      <div class="config-item"><span class="config-key">Stop Loss</span><span class="config-value text-red">${rs.stop_loss_pct || 5}%</span></div>
      <div class="config-item"><span class="config-key">Take Profit</span><span class="config-value text-green">${rs.take_profit_pct || 10}%</span></div>
      <div class="config-item"><span class="config-key">Limite Di\u00E1rio de Perda</span><span class="config-value">${fmtMoney(rs.daily_loss_limit?.max_loss)}</span></div>
      <div class="config-item"><span class="config-key">Trades \u00DAltimas 1h</span><span class="config-value">${rs.trade_limits?.trades_last_hour || 0}</span></div>
      <div class="config-item"><span class="config-key">Trades Hoje</span><span class="config-value">${rs.trade_limits?.trades_today || 0}</span></div>
      <div class="config-item"><span class="config-key">Posi\u00E7\u00F5es Abertas</span><span class="config-value">${tradePos}</span></div>
    `;

    // IRQ Signals
    const lockBadge = document.getElementById('risk-lock-badge');
    lockBadge.textContent = rs.is_locked ? '\uD83D\uDD12 BLOQUEADO' : '\uD83D\uDFE2 OK';
    lockBadge.className = `badge ${rs.is_locked ? 'badge-red' : 'badge-green'}`;

    const irqBadgeEl = document.getElementById('risk-irq-badge-page');
    if (irq) {
      const level = irq.level || irq.protection_level;
      irqBadgeEl.textContent = `${irq.color} ${level}`;
      irqBadgeEl.className = `badge ${irqBadgeClass(level)}`;
      document.getElementById('risk-signals-list').innerHTML = renderSignalsInline(irq);
    } else {
      document.getElementById('risk-signals-list').innerHTML = '<div class="loading-overlay">An\u00E1lise n\u00E3o dispon\u00EDvel</div>';
    }

    // Limits progress - usando dados reais do trade
    const limits = rs.daily_loss_limit;
    const trades = rs.trade_limits;
    const dailyUsed = limits ? Math.abs(dailyPnl / limits.max_loss) * 100 : 0;
    const tradeHourUsed = trades ? (trades.trades_last_hour / 20) * 100 : 0;
    const tradeDayUsed = trades ? (trades.trades_today / 100) * 100 : 0;

    document.getElementById('risk-limits-section').innerHTML = `
      <div style="margin-bottom:16px">
        <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:6px">
          <span class="text-muted">Perda Di\u00E1ria Usada</span>
          <span>${fmtMoney(dailyPnl)} / ${fmtMoney(limits?.max_loss)}</span>
        </div>
        <div class="progress-bar" style="height:10px">
          <div class="progress-fill ${dailyUsed > 80 ? 'red' : dailyUsed > 50 ? 'yellow' : 'green'}" style="width:${Math.min(dailyUsed,100)}%"></div>
        </div>
      </div>
      <div style="margin-bottom:16px">
        <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:6px">
          <span class="text-muted">Trades \u00DAltimas 1h</span>
          <span>${trades?.trades_last_hour || 0} / 20</span>
        </div>
        <div class="progress-bar" style="height:10px">
          <div class="progress-fill ${tradeHourUsed > 80 ? 'red' : tradeHourUsed > 50 ? 'yellow' : 'blue'}" style="width:${Math.min(tradeHourUsed,100)}%"></div>
        </div>
      </div>
      <div>
        <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:6px">
          <span class="text-muted">Trades Hoje</span>
          <span>${trades?.trades_today || 0} / 100</span>
        </div>
        <div class="progress-bar" style="height:10px">
          <div class="progress-fill ${tradeDayUsed > 80 ? 'red' : tradeDayUsed > 50 ? 'yellow' : 'blue'}" style="width:${Math.min(tradeDayUsed,100)}%"></div>
        </div>
      </div>
    `;

    timeEl.textContent = `Atualizado: ${new Date().toLocaleTimeString('pt-BR')}`;

  } catch (e) {
    toast('Erro ao carregar dados de risco', 'error');
  }
}

// =============================================
// PAGE: HISTÓRICO
// =============================================

async function loadHistoryPage() {
  loadHistoryAnalysis();
  loadDbStats();
}

async function loadHistoryAnalysis() {
  const el = document.getElementById('history-analysis-content');
  el.innerHTML = '<div class="loading-overlay"><div class="spinner"></div> Carregando...</div>';
  try {
    const data = await api('/db/analysis-history');
    const items = data.data || [];
    if (!items.length) {
      el.innerHTML = '<div class="empty-state"><div class="empty-icon">📊</div><p>Nenhuma análise registrada ainda.<br>Execute uma análise para começar o histórico.</p></div>';
      return;
    }
    let html = '<div class="table-wrap"><table><thead><tr><th>Data/Hora</th><th>IRQ</th><th>Nível</th><th>Melhor Ativo</th><th>Score</th></tr></thead><tbody>';
    for (const row of items) {
      html += `<tr>
        <td>${row.timestamp ? new Date(row.timestamp).toLocaleString('pt-BR') : '--'}</td>
        <td>${row.irq_score != null ? (row.irq_score * 100).toFixed(1) + '%' : '--'}</td>
        <td><span class="badge ${irqBadgeClass(row.protection_level)}">${row.protection_level || '--'}</span></td>
        <td>${row.best_asset || '--'}</td>
        <td>${row.best_score != null ? row.best_score.toFixed(4) : '--'}</td>
      </tr>`;
    }
    html += '</tbody></table></div>';
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = '<div class="empty-state"><p>Erro ao carregar histórico</p></div>';
  }
}

async function loadHistoryTrades() {
  const el = document.getElementById('history-trades-content');
  el.innerHTML = '<div class="loading-overlay"><div class="spinner"></div> Carregando...</div>';
  try {
    const data = await api('/db/trades');
    const items = data.data || [];
    if (!items.length) {
      el.innerHTML = '<div class="empty-state"><div class="empty-icon">📚</div><p>Nenhum trade registrado ainda.</p></div>';
      return;
    }
    let html = '<div class="table-wrap"><table><thead><tr><th>Data/Hora</th><th>Ativo</th><th>Tipo</th><th>Quantidade</th><th>Preço</th><th>P&L</th></tr></thead><tbody>';
    for (const row of items) {
      html += `<tr>
        <td>${row.timestamp ? new Date(row.timestamp).toLocaleString('pt-BR') : '--'}</td>
        <td><strong>${row.asset || '--'}</strong></td>
        <td>${actionBadge(row.trade_type)}</td>
        <td>${row.quantity != null ? row.quantity.toFixed(6) : '--'}</td>
        <td>${fmtPrice(row.price)}</td>
        <td class="${row.pnl >= 0 ? 'text-green' : 'text-red'}">${row.pnl != null ? fmtMoney(row.pnl) : '--'}</td>
      </tr>`;
    }
    html += '</tbody></table></div>';
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = '<div class="empty-state"><p>Erro ao carregar trades</p></div>';
  }
}

async function loadDbStats() {
  const el = document.getElementById('db-stats-cards');
  try {
    const data = await api('/db/stats');
    const stats = data.data || {};
    const icons = { portfolios: '💼', positions: '📊', trades: '🔄', analysis_history: '📈', market_snapshots: '📸' };
    const labels = { portfolios: 'Portfólios', positions: 'Posições', trades: 'Trades', analysis_history: 'Análises', market_snapshots: 'Snapshots' };
    let html = '';
    for (const [key, val] of Object.entries(stats)) {
      html += `<div class="card">
        <div class="card-header">
          <span class="card-title">${labels[key] || key}</span>
          <div class="card-icon blue">${icons[key] || '📊'}</div>
        </div>
        <div class="kpi-value">${val}</div>
        <div class="kpi-label">registros</div>
      </div>`;
    }
    el.innerHTML = html || '<div class="empty-state" style="grid-column:1/-1"><p>Sem dados</p></div>';
  } catch (e) {
    el.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><p>Erro ao carregar estatísticas</p></div>';
  }
}

// =============================================
// PAGE: TRADE
// =============================================

let _tradeLogCache = [];  // full log for client-side filtering

async function loadTradePage() {
  try {
    const [res, perfRes] = await Promise.all([
      api('/trade/status'),
      api('/performance')
    ]);
    if (!res.success) return;
    const d = res.data;

    // Valor Total card — 3 timeframes + hoje + total
    const perf = perfRes?.data || {};
    const today = new Date().toISOString().slice(0, 10);
    const todayCycles = (perf.recent_cycles || []).filter(c => (c.timestamp || '').startsWith(today));
    const todayPnl  = perf.pnl_today   != null ? perf.pnl_today   : todayCycles.reduce((s, c) => s + (c.pnl || 0), 0);
    const totalPnl  = perf.total_pnl   || d.total_pnl || 0;
    const totalCycles = perf.total_cycles || 0;

    // Capital display
    const capDisplayEl = document.getElementById('trade-capital-display');
    if (capDisplayEl) capDisplayEl.textContent = fmtMoney(d.capital_efetivo || d.capital);
    const tradeCurrentCapEl = document.getElementById('trade-current-capital');
    if (tradeCurrentCapEl) tradeCurrentCapEl.textContent = fmtMoney(d.capital);
    const tradePnlHojeEl = document.getElementById('trade-pnl-hoje');
    if (tradePnlHojeEl) {
      const ph = d.pnl_hoje ?? todayPnl;
      tradePnlHojeEl.textContent = (ph >= 0 ? '+' : '') + fmtMoney(ph);
      tradePnlHojeEl.style.color = ph >= 0 ? 'var(--green)' : 'var(--red)';
    }
    const tradeCapEfetivoEl = document.getElementById('trade-capital-efetivo');
    if (tradeCapEfetivoEl) {
      tradeCapEfetivoEl.textContent = fmtMoney(d.capital_efetivo || d.capital);
      tradeCapEfetivoEl.style.color = (d.pnl_hoje ?? todayPnl) >= 0 ? 'var(--green)' : 'var(--red)';
    }

    // ⚡ 5min
    const gain5mEl = document.getElementById('trade-gain-5m');
    if (gain5mEl) {
      const v = perf.pnl_today_5m || 0;
      gain5mEl.textContent = fmtMoney(v);
      gain5mEl.style.color = v >= 0 ? 'var(--green)' : 'var(--red)';
    }
    // 🕐 1h
    const gain1hEl = document.getElementById('trade-gain-1h');
    if (gain1hEl) {
      const v = perf.pnl_today_1h || 0;
      gain1hEl.textContent = fmtMoney(v);
      gain1hEl.style.color = v >= 0 ? 'var(--green)' : 'var(--red)';
    }
    // 📅 1d
    const gain1dEl = document.getElementById('trade-gain-1d');
    if (gain1dEl) {
      const v = perf.pnl_today_1d || 0;
      gain1dEl.textContent = fmtMoney(v);
      gain1dEl.style.color = v >= 0 ? 'var(--green)' : 'var(--red)';
    }
    // Ganho Hoje + Perda Hoje
    const gainTodayEl = document.getElementById('trade-gain-today');
    const lossTodayEl = document.getElementById('trade-loss-today');
    const todayGainVal = perf.today_gain ?? Math.max(0, todayPnl);
    const todayLossVal = perf.today_loss ?? 0;
    if (gainTodayEl) {
      gainTodayEl.textContent = fmtMoney(todayGainVal);
      gainTodayEl.style.color = 'var(--green)';
    }
    if (lossTodayEl) {
      if (todayLossVal > 0) {
        lossTodayEl.style.display = 'block';
        lossTodayEl.textContent = '-' + fmtMoney(todayLossVal);
      } else {
        lossTodayEl.style.display = 'none';
      }
    }
    const gainTodayCyclesEl = document.getElementById('trade-gain-today-cycles');
    if (gainTodayCyclesEl) gainTodayCyclesEl.textContent = `${perf.today_cycles ?? todayCycles.length} ciclo${(perf.today_cycles ?? todayCycles.length) !== 1 ? 's' : ''} hoje`;
    // Ganho Total + Perda Total
    const gainTotalEl = document.getElementById('trade-gain-total');
    const lossTotalEl = document.getElementById('trade-loss-total');
    const totalGainVal = perf.total_gain ?? Math.max(0, totalPnl);
    const totalLossVal = perf.total_loss ?? 0;
    if (gainTotalEl) {
      gainTotalEl.textContent = fmtMoney(totalGainVal);
      gainTotalEl.style.color = 'var(--green)';
    }
    if (lossTotalEl) {
      if (totalLossVal > 0) {
        lossTotalEl.style.display = 'block';
        lossTotalEl.textContent = '-' + fmtMoney(totalLossVal);
      } else {
        lossTotalEl.style.display = 'none';
      }
    }
    const gainTotalCyclesEl = document.getElementById('trade-gain-total-cycles');
    if (gainTotalCyclesEl) gainTotalCyclesEl.textContent = `${totalCycles} ciclo${totalCycles !== 1 ? 's' : ''} no total`;
    // Banner reinvestimento
    const reinvestBanner = document.getElementById('trade-reinvest-info');
    if (reinvestBanner) reinvestBanner.style.display = 'block';


    // Status bar
    const isActive = d.auto_trading;
    const badge = document.getElementById('trade-bot-badge');
    if (badge) {
      badge.textContent = isActive ? '🟢 ATIVO' : '⏸ PARADO';
      badge.className = `badge ${isActive ? 'badge-green' : 'badge-gray'}`;
    }
    const toggleBtn = document.getElementById('trade-toggle-btn');
    if (toggleBtn) {
      toggleBtn.textContent = isActive ? '⏸ Pausar Bot' : 'â–¶ Iniciar Bot';
      toggleBtn.className = `btn ${isActive ? 'btn-secondary' : 'btn-primary'}`;
    }
    const capitalBadge = document.getElementById('trade-capital-badge');
    if (capitalBadge) capitalBadge.textContent = fmtMoney(d.capital_efetivo || d.capital);

    // Sessão atual
    const sessionBadge = document.getElementById('trade-session-badge');
    if (sessionBadge) {
      const b3Open = d.b3_open ?? _is_market_open_js();
      sessionBadge.textContent = b3Open ? '🇧🇷 B3 + 🌍 Crypto' : '🌍 Crypto 24/7';
      sessionBadge.className = `badge ${b3Open ? 'badge-green' : 'badge-blue'}`;
    }

    const lastCycle = document.getElementById('trade-last-cycle');
    if (lastCycle) {
      lastCycle.textContent = d.last_cycle
        ? new Date(d.last_cycle).toLocaleString('pt-BR')
        : '—';
    }

    // Capital card
    const capEl = document.getElementById('trade-current-capital');
    if (capEl) capEl.textContent = fmtMoney(d.capital);
    const ceEl = document.getElementById('trade-capital-efetivo');
    if (ceEl) {
      ceEl.textContent = fmtMoney(d.capital_efetivo || d.capital);
      ceEl.style.color = (d.pnl_hoje ?? 0) >= 0 ? 'var(--green)' : 'var(--red)';
    }
    const pnlEl = document.getElementById('trade-total-pnl');
    if (pnlEl) {
      pnlEl.textContent = fmtMoney(d.total_pnl || 0);
      pnlEl.style.color = (d.total_pnl || 0) >= 0 ? 'var(--green)' : 'var(--red)';
    }

    // Pre-fill deposit input with current capital
    const inp = document.getElementById('trade-capital-input');
    if (inp && !inp.value) inp.placeholder = `Atual: R$ ${(d.capital || 0).toFixed(2)}`;

    // Positions mini + table + donut
    const positions = d.positions || {};
    const posEntries = Object.entries(positions);

    const countBadge = document.getElementById('trade-positions-count');
    if (countBadge) countBadge.textContent = `${posEntries.length} ativos`;

    // Mini list
    const miniEl = document.getElementById('trade-positions-mini');
    if (miniEl) {
      if (!posEntries.length) {
        miniEl.innerHTML = '<div class="empty-state" style="padding:16px"><p style="font-size:12px">Sem posições</p></div>';
      } else {
        miniEl.innerHTML = posEntries
          .sort(([, a], [, b]) => b.amount - a.amount)
          .map(([asset, p]) => `
            <div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid var(--border)">
              <span style="font-weight:700;font-size:13px">${asset}</span>
              <span style="font-size:12px;color:var(--text-muted)">${p.pct}%</span>
              <span style="font-size:13px;color:var(--text-primary)">${fmtMoney(p.amount)}</span>
              ${actionBadge(p.action)}
            </div>`).join('');
      }
    }

    // Positions table
    const tbody = document.getElementById('trade-positions-tbody');
    if (tbody) {
      if (!posEntries.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--text-muted)">Execute um ciclo para ver as posições</td></tr>';
      } else {
        tbody.innerHTML = posEntries
          .sort(([, a], [, b]) => b.amount - a.amount)
          .map(([asset, p]) => `<tr>
            <td><strong>${asset}</strong></td>
            <td>${classifBadge(p.classification || p.action)}</td>
            <td style="font-weight:700">${fmtMoney(p.amount)}</td>
            <td>
              <div style="display:flex;align-items:center;gap:8px">
                <div style="background:var(--bg-tertiary);border-radius:4px;height:6px;width:80px;overflow:hidden">
                  <div style="height:6px;border-radius:4px;background:var(--green);width:${Math.min(p.pct, 100)}%"></div>
                </div>
                <span style="font-size:12px;color:var(--text-muted)">${p.pct}%</span>
              </div>
            </td>
            <td class="${(p.change_pct || 0) >= 0 ? 'text-green' : 'text-red'}">${p.change_pct != null ? (p.change_pct >= 0 ? '+' : '') + p.change_pct.toFixed(1) + '%' : '—'}</td>
            <td>${actionBadge(p.action)}</td>
          </tr>`).join('');
      }
    }

    // Donut chart
    destroyChart('trade-donut');
    const donutCtx = document.getElementById('chart-trade-donut');
    const nonZero = posEntries.filter(([, p]) => p.amount > 0);
    if (nonZero.length > 0 && donutCtx) {
      const palette = ['#388bfd','#3fb950','#d29922','#f85149','#db6d28','#8b949e','#a5d6ff','#7ee787','#ff9500','#bf00ff'];
      charts['trade-donut'] = new Chart(donutCtx, {
        type: 'doughnut',
        data: {
          labels: nonZero.map(([a]) => a),
          datasets: [{
            data: nonZero.map(([, p]) => p.amount),
            backgroundColor: palette.slice(0, nonZero.length),
            borderWidth: 2,
            borderColor: '#1c2128',
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'right', labels: { color: '#8b949e', font: { size: 11 } } }
          }
        }
      });
    }

    // Cycle time
    const ctEl = document.getElementById('trade-cycle-time');
    if (ctEl) ctEl.textContent = d.last_cycle ? `Ciclo: ${new Date(d.last_cycle).toLocaleString('pt-BR')}` : '';

    // Log
    _tradeLogCache = d.log || [];
    renderTradeLog(_tradeLogCache);

  } catch (e) {
    toast('Erro ao carregar Trade', 'error');
  }
}

function renderTradeLog(entries) {
  const tbody = document.getElementById('trade-log-tbody');
  if (!tbody) return;
  if (!entries.length) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--text-muted)">Nenhum evento registrado ainda. Deposite capital e execute um ciclo.</td></tr>';
    return;
  }

  const typeStyles = {
    'COMPRA':    { cls: 'badge-green',  icon: '📈' },
    'VENDA':     { cls: 'badge-red',    icon: '📉' },
    'HOLD':      { cls: 'badge-gray',   icon: '⏸' },
    'DEPÓSITO':  { cls: 'badge-blue',   icon: '💰' },
    'RETIRADA':  { cls: 'badge-yellow', icon: '💸' },
    'CICLO':     { cls: 'badge-blue',   icon: '🔄' },
    'SISTEMA':   { cls: 'badge-gray',   icon: 'âš™️' },
    'ERRO':      { cls: 'badge-red',    icon: '❌' },
  };

  tbody.innerHTML = entries.map(ev => {
    const style = typeStyles[ev.type] || { cls: 'badge-gray', icon: '•' };
    const ts = ev.timestamp ? new Date(ev.timestamp).toLocaleString('pt-BR') : '—';
    const amtHtml = ev.amount > 0 ? `<span style="font-weight:700">${fmtMoney(ev.amount)}</span>` : '<span style="color:var(--text-muted)">—</span>';
    return `<tr>
      <td style="white-space:nowrap;font-size:12px;color:var(--text-muted)">${ts}</td>
      <td><span class="badge ${style.cls}">${style.icon} ${ev.type}</span></td>
      <td><strong>${ev.asset || '—'}</strong></td>
      <td>${amtHtml}</td>
      <td style="font-size:12px;color:var(--text-muted)">${ev.note || '—'}</td>
    </tr>`;
  }).join('');
}

function filterTradeLog() {
  const filter = document.getElementById('trade-log-filter')?.value || 'all';
  const filtered = filter === 'all'
    ? _tradeLogCache
    : _tradeLogCache.filter(ev => ev.type === filter);
  renderTradeLog(filtered);
}

async function depositCapital() {
  const inp = document.getElementById('trade-capital-input');
  const val = parseFloat(inp?.value);
  if (!val || val <= 0) { toast('Digite um valor válido', 'warning'); return; }
  try {
    const res = await api('/trade/capital', {
      method: 'POST',
      body: JSON.stringify({ amount: val }),
    });
    if (res.success) {
      toast(`💰 Capital atualizado para ${fmtMoney(res.capital)}`, 'success');
      if (inp) inp.value = '';
      await loadTradePage();
    }
  } catch (e) {
    toast('Erro ao atualizar capital', 'error');
  }
}

async function toggleTrading() {
  try {
    const badge = document.getElementById('trade-bot-badge');
    const isActive = badge?.textContent?.includes('ATIVO');
    const endpoint = isActive ? '/trade/stop' : '/trade/start';
    const res = await api(endpoint, { method: 'POST' });
    if (res.success) {
      toast(res.auto_trading ? '✅ Bot iniciado' : '⏸ Bot pausado', 'success');
      await loadTradePage();
    }
  } catch (e) {
    toast('Erro ao alterar estado do bot', 'error');
  }
}

async function runTradeCycle() {
  const btn = document.querySelector('[onclick="runTradeCycle()"]');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Processando...'; }
  try {
    const res = await api('/trade/cycle', { method: 'POST' });
    if (res.success) {
      toast(`🔄 Ciclo executado — ${res.data?.assets_analyzed || 0} ativos analisados`, 'success');
      await loadTradePage();
    } else {
      toast('Sem dados de mercado para ciclo', 'warning');
    }
  } catch (e) {
    toast('Erro ao executar ciclo', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔄 Executar Ciclo'; }
  }
}

async function clearTradeLog() {
  if (!confirm('Limpar todo o histórico de atividade?')) return;
  try {
    // Clear via local state (server keeps its own)
    _tradeLogCache = [];
    renderTradeLog([]);
    toast('Histórico limpo', 'info');
  } catch (e) { /* silent */ }
}

// =============================================
// PAGE: CONFIGURAÇÕES
// =============================================

async function loadSettings() {
  try {
    const data = await api('/modules');
    if (!data.success) return;

    const modules = data.data;
    const enginesEl = document.getElementById('settings-modules');
    const configEl = document.getElementById('settings-config');

    const allEngines = { ...modules.engines, ...modules.integrations };
    let html = '';
    for (const [key, val] of Object.entries(allEngines)) {
      const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      html += `<div class="config-item">
        <span class="config-key">${label}</span>
        <span class="badge ${val ? 'badge-green' : 'badge-red'}">${val ? '✅ Online' : '❌ Offline'}</span>
      </div>`;
    }
    enginesEl.innerHTML = html;

    const cfg = modules.config || {};
    let cfgHtml = '';
    const cfgLabels = {
      initial_capital: 'Capital Inicial',
      stop_loss: 'Stop Loss',
      take_profit: 'Take Profit',
      max_daily_loss: 'Perda Máx. Diária',
      max_trades_hour: 'Trades/Hora (máx)',
      max_trades_day: 'Trades/Dia (máx)',
    };
    for (const [k, v] of Object.entries(cfg)) {
      if (k === 'allowed_assets') continue;
      cfgHtml += `<div class="config-item">
        <span class="config-key">${cfgLabels[k] || k}</span>
        <span class="config-value">${v}</span>
      </div>`;
    }
    if (cfg.allowed_assets) {
      cfgHtml += `<div class="config-item">
        <span class="config-key">Ativos Permitidos</span>
        <span style="display:flex;gap:4px;flex-wrap:wrap">${cfg.allowed_assets.map(a => `<span class="badge badge-gray">${a}</span>`).join('')}</span>
      </div>`;
    }
    configEl.innerHTML = cfgHtml;

  } catch (e) {
    toast('Erro ao carregar configurações', 'error');
  }
}

// =============================================
// INDICADORES TÉCNICOS
// =============================================

// Armazena dados globais para filtragem/ordenação sem nova chamada à API
let _indicatorsData = {};
let _indicatorsSortKey = 'consensus';
let _indicatorsSortAsc = true;

// Classifica o tipo do ativo
const _B3_LIST = ['PETR4','PRIO3','CSAN3','EGIE3','VALE3','ITUB4','BBDC4','ABEV3','WEGE3','RENT3','MGLU3','BBAS3','VBBR3','LREN3','SUZB3','RDOR3','B3SA3','BPAC11','GGBR4','SBSP3'];
const _CRYPTO_LIST = ['BTCUSDT','ETHUSDT','BNBUSDT','ADAUSDT','XRPUSDT','SOLUSDT','DOTUSDT','LINKUSDT','LTCUSDT','UNIUSDT'];
function _assetType(asset) {
  if (_CRYPTO_LIST.includes(asset)) return 'CRYPTO';
  if (_B3_LIST.includes(asset)) return 'B3';
  return 'US';
}

async function loadIndicators() {
  const interval = document.getElementById('indicators-interval')?.value || '5m';
  const tbody = document.getElementById('indicators-all-tbody');
  tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:32px;color:var(--text-muted)">
    <div class="spinner" style="margin:0 auto 12px"></div>Buscando indicadores para 80 ativos (pode levar ~20s)...
  </td></tr>`;
  // Reset summary while loading
  ['ind-count-compra','ind-count-venda','ind-count-neutro','ind-avg-rsi'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = '...';
  });
  try {
    const res = await api(`/market/indicators-all?interval=${interval}`, {}, 90000);
    _indicatorsData = res.data || {};
    const assets = Object.entries(_indicatorsData);
    if (!assets.length) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:32px;color:var(--text-muted)">Nenhum dado disponível</td></tr>';
      return;
    }
    // Summary cards
    const compraCount = assets.filter(([,d]) => d.consensus === 'COMPRA').length;
    const vendaCount  = assets.filter(([,d]) => d.consensus === 'VENDA').length;
    const neutroCount = assets.filter(([,d]) => d.consensus === 'NEUTRO').length;
    const rsiValues   = assets.map(([,d]) => d.rsi).filter(v => v != null && v > 0);
    const avgRsi      = rsiValues.length ? rsiValues.reduce((a, b) => a + b, 0) / rsiValues.length : 0;
    document.getElementById('ind-count-compra').textContent = compraCount;
    document.getElementById('ind-count-venda').textContent = vendaCount;
    document.getElementById('ind-count-neutro').textContent = neutroCount;
    document.getElementById('ind-avg-rsi').textContent = avgRsi.toFixed(1);
    document.getElementById('ind-avg-rsi-label').textContent =
      avgRsi < 40 ? '📉 Mercado sobrevendido' : avgRsi > 60 ? '📈 Mercado sobrecomprado' : '↔️ Mercado neutro';
    document.getElementById('indicators-update-time').textContent = `Atualizado: ${new Date().toLocaleTimeString()}`;
    setLastUpdate();
    // Render table
    filterIndicatorsTable();
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:32px;color:var(--red)">
      ⚠️ Erro ao carregar: ${e.message}
    </td></tr>`;
  }
}

function filterIndicatorsTable() {
  const consensusFilter = document.getElementById('indicators-filter-consensus')?.value || '';
  const typeFilter      = document.getElementById('indicators-filter-type')?.value || '';
  const search          = (document.getElementById('indicators-search')?.value || '').toUpperCase().trim();
  const tbody           = document.getElementById('indicators-all-tbody');

  let entries = Object.entries(_indicatorsData);
  if (!entries.length) return;

  // Filter
  if (consensusFilter) entries = entries.filter(([,d]) => d.consensus === consensusFilter);
  if (typeFilter)      entries = entries.filter(([a]) => _assetType(a) === typeFilter);
  if (search)          entries = entries.filter(([a]) => a.includes(search));

  // Sort
  entries.sort(([aKey, aVal], [bKey, bVal]) => {
    let av, bv;
    switch (_indicatorsSortKey) {
      case 'asset':     av = aKey; bv = bKey; break;
      case 'price':     av = aVal.price || 0; bv = bVal.price || 0; break;
      case 'rsi':       av = aVal.rsi || 0; bv = bVal.rsi || 0; break;
      case 'boll':      av = aVal.boll_position || 50; bv = bVal.boll_position || 50; break;
      case 'consensus': av = aVal.consensus; bv = bVal.consensus; break;
      default:          av = aKey; bv = bKey;
    }
    if (av < bv) return _indicatorsSortAsc ? -1 : 1;
    if (av > bv) return _indicatorsSortAsc ? 1 : -1;
    return 0;
  });

  document.getElementById('ind-showing-count').textContent = `${entries.length} ativos`;

  if (!entries.length) {
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:24px;color:var(--text-muted)">Nenhum ativo corresponde aos filtros</td></tr>';
    return;
  }

  tbody.innerHTML = entries.map(([asset, d]) => {
    const rsiNum   = d.rsi || 0;
    const rsiZone  = rsiNum < 30 ? 'SOBREVENDIDO' : rsiNum > 70 ? 'SOBRECOMPRADO' : 'NEUTRO';
    const rsiClass = rsiNum < 30 ? 'green' : rsiNum > 70 ? 'red' : 'text-muted';
    const crossCls = d.macd_cross === 'COMPRA' ? 'green' : d.macd_cross === 'VENDA' ? 'red' : 'text-muted';
    const bollPos  = d.boll_position != null ? d.boll_position : 50;
    const bollCls  = bollPos < 20 ? 'green' : bollPos > 80 ? 'red' : '';
    const consBadge = d.consensus === 'COMPRA' ? 'badge-green' : d.consensus === 'VENDA' ? 'badge-red' : 'badge-gray';
    const rowBg    = d.consensus === 'COMPRA'
      ? 'background:rgba(0,255,136,0.04)'
      : d.consensus === 'VENDA'
        ? 'background:rgba(255,68,68,0.04)'
        : '';
    const type = _assetType(asset);
    const typeBadge = type === 'B3' ? 'badge-blue' : type === 'CRYPTO' ? 'badge-yellow' : 'badge-gray';

    return `<tr style="${rowBg};cursor:pointer" onclick="analyzeFromTable('${asset}')">
      <td><strong>${asset}</strong></td>
      <td><span class="badge ${typeBadge}" style="font-size:10px">${type}</span></td>
      <td>${fmtPrice(d.price)}</td>
      <td class="${rsiClass}" style="font-weight:700">${rsiNum.toFixed(1)}</td>
      <td style="font-size:11px" class="${rsiClass}">${rsiZone}</td>
      <td class="${d.macd_trend === 'ALTA' ? 'green' : 'red'}">${d.macd_trend || '--'}</td>
      <td class="${crossCls}" style="font-weight:600">${d.macd_cross || '--'}</td>
      <td class="${bollCls}">${bollPos.toFixed(0)}%</td>
      <td><span class="badge ${consBadge}">${d.consensus}</span></td>
    </tr>`;
  }).join('');
}

function sortIndicatorsBy(key) {
  if (_indicatorsSortKey === key) {
    _indicatorsSortAsc = !_indicatorsSortAsc;
  } else {
    _indicatorsSortKey = key;
    _indicatorsSortAsc = true;
  }
  filterIndicatorsTable();
}

// Chamado ao clicar na linha da tabela
function analyzeFromTable(asset) {
  const input = document.getElementById('indicator-asset-input');
  if (input) input.value = asset;
  loadSingleIndicator();
}

async function loadSingleIndicator() {
  const asset = document.getElementById('indicator-asset-input')?.value?.trim()?.toUpperCase();
  if (!asset) { toast('Digite um ativo', 'warning'); return; }
  const interval = document.getElementById('indicator-detail-interval')?.value
    || document.getElementById('indicators-interval')?.value
    || '5m';
  const container = document.getElementById('indicator-detail-content');
  container.innerHTML = '<div class="loading-overlay"><div class="spinner"></div> Analisando ' + asset + '...</div>';
  try {
    const res = await api(`/market/indicators/${asset}?interval=${interval}`, {}, 30000);
    const d = res.data || {};
    const s = d.summary || {};

    // Header com preço e volatilidade
    let html = `<div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:12px 4px 16px;border-bottom:1px solid var(--border);margin-bottom:12px">
      <div>
        <div style="font-size:11px;color:var(--text-muted)">ATIVO</div>
        <div style="font-size:22px;font-weight:800;color:var(--green)">${asset}</div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text-muted)">PREÇO</div>
        <div style="font-size:18px;font-weight:700">${fmtPrice(s.current_price)}</div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text-muted)">HIGH 20</div>
        <div style="font-size:15px;font-weight:600;color:var(--green)">${fmtPrice(s.high)}</div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text-muted)">LOW 20</div>
        <div style="font-size:15px;font-weight:600;color:var(--red)">${fmtPrice(s.low)}</div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text-muted)">VOLATILIDADE</div>
        <div style="font-size:15px;font-weight:600;color:var(--yellow)">${s.volatility_pct?.toFixed(2) || '0'}%</div>
      </div>
      <div style="margin-left:auto">
        <div style="font-size:11px;color:var(--text-muted)">TIMEFRAME</div>
        <div class="badge badge-blue">${interval} | ${d.candles || '--'} candles</div>
      </div>
    </div>`;

    html += `<div class="cards-grid cards-3">`;

    // RSI card
    const rsiVal = d.rsi || 0;
    const rsiZone = rsiVal < 30 ? 'Sobrevendido 📉' : rsiVal > 70 ? 'Sobrecomprado 📈' : 'Zona Neutra ↔️';
    const rsiColor = rsiVal < 30 ? 'var(--green)' : rsiVal > 70 ? 'var(--red)' : 'var(--text-primary)';
    html += `<div class="card" style="padding:14px">
      <div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">RSI (14)</div>
      <div style="font-size:36px;font-weight:800;color:${rsiColor};line-height:1">${rsiVal.toFixed(1)}</div>
      <div style="font-size:12px;margin-top:6px;color:${rsiColor}">${rsiZone}</div>
      <div style="margin-top:8px">
        <div style="height:6px;background:var(--bg-tertiary);border-radius:3px;overflow:hidden">
          <div style="height:100%;width:${rsiVal}%;background:${rsiColor};border-radius:3px;transition:width .5s"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-muted);margin-top:2px"><span>0</span><span>30</span><span>70</span><span>100</span></div>
      </div>
    </div>`;

    // MACD card
    const macd = d.macd || {};
    const macdColor = macd.crossover === 'COMPRA' ? 'var(--green)' : macd.crossover === 'VENDA' ? 'var(--red)' : 'var(--text-primary)';
    html += `<div class="card" style="padding:14px">
      <div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">MACD (12,26,9)</div>
      <div style="font-size:24px;font-weight:800;color:${macdColor}">${macd.trend || '--'}</div>
      <div style="font-size:12px;margin-top:4px">Cross: <strong style="color:${macdColor}">${macd.crossover || '--'}</strong></div>
      <div style="margin-top:8px;font-size:11px;color:var(--text-muted);display:flex;flex-direction:column;gap:2px">
        <span>MACD: <strong>${macd.macd?.toFixed(5) || '--'}</strong></span>
        <span>Signal: <strong>${macd.signal?.toFixed(5) || '--'}</strong></span>
        <span>Histograma: <strong style="color:${(macd.histogram||0) > 0 ? 'var(--green)' : 'var(--red)'}">${macd.histogram?.toFixed(5) || '--'}</strong></span>
      </div>
    </div>`;

    // Bollinger card
    const boll = d.bollinger || {};
    const bollPos = boll.position ?? 50;
    const bollColor = bollPos < 20 ? 'var(--green)' : bollPos > 80 ? 'var(--red)' : 'var(--yellow)';
    html += `<div class="card" style="padding:14px">
      <div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">Bollinger Bands (20,2)</div>
      <div style="font-size:30px;font-weight:800;color:${bollColor}">${bollPos.toFixed(0)}%</div>
      <div style="font-size:11px;margin-top:4px;color:var(--text-muted)">dentro da banda</div>
      <div style="margin-top:8px;font-size:11px;color:var(--text-muted);display:flex;flex-direction:column;gap:2px">
        <span>SMA: <strong>${boll.sma || '--'}</strong></span>
        <span style="color:var(--red)">Superior: <strong>${boll.upper || '--'}</strong></span>
        <span style="color:var(--green)">Inferior: <strong>${boll.lower || '--'}</strong></span>
        <span>BW: <strong>${boll.bandwidth || '--'}%</strong></span>
      </div>
    </div>`;

    // Stochastic card
    const stoch = d.stochastic || {};
    const stochColor = stoch.zone === 'SOBRECOMPRADO' ? 'var(--red)' : stoch.zone === 'SOBREVENDIDO' ? 'var(--green)' : 'var(--text-primary)';
    html += `<div class="card" style="padding:14px">
      <div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">Estocástico (14,3)</div>
      <div style="font-size:24px;font-weight:800;color:${stochColor}">K: ${stoch.k?.toFixed(1) || '--'}</div>
      <div style="font-size:14px;color:var(--text-muted)">D: ${stoch.d?.toFixed(1) || '--'}</div>
      <div style="margin-top:6px;font-size:12px;color:${stochColor}">${stoch.zone || '--'}</div>
      <div style="font-size:12px;margin-top:4px">Cross: <strong style="color:${stoch.crossover === 'COMPRA' ? 'var(--green)' : stoch.crossover === 'VENDA' ? 'var(--red)' : ''}">${stoch.crossover || '--'}</strong></div>
    </div>`;

    // Fibonacci card
    const fib = d.fibonacci || {};
    const fibLevels = fib.levels || {};
    html += `<div class="card" style="padding:14px">
      <div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">Fibonacci Retracement</div>
      <div style="font-size:20px;font-weight:800;color:var(--yellow)">Nível ${fib.nearest_level || '--'}%</div>
      <div style="font-size:12px;margin-top:4px">Preço: <strong>${fmtPrice(fib.nearest_price)}</strong></div>
      <div style="font-size:12px">Trend: <strong style="color:${fib.trend === 'ALTA' ? 'var(--green)' : 'var(--red)'}">${fib.trend || '--'}</strong></div>
      <div style="margin-top:8px;font-size:10px;color:var(--text-muted);display:flex;flex-direction:column;gap:1px">
        ${Object.entries(fibLevels).map(([k,v]) => `<span>${k}%: <strong>${v}</strong>${fib.nearest_level === k ? ' ◀' : ''}</span>`).join('')}
      </div>
    </div>`;

    // VWAP card
    const vwap = d.vwap || {};
    const vwapColor = vwap.signal === 'COMPRA' ? 'var(--green)' : vwap.signal === 'VENDA' ? 'var(--red)' : 'var(--text-primary)';
    html += `<div class="card" style="padding:14px">
      <div style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">VWAP</div>
      <div style="font-size:22px;font-weight:800">${fmtPrice(vwap.vwap)}</div>
      <div style="font-size:12px;margin-top:4px;color:${vwapColor}">${vwap.position || '--'} (${vwap.deviation_pct?.toFixed(2) || '0'}%)</div>
      <div style="font-size:12px;margin-top:4px">Sinal: <strong style="color:${vwapColor}">${vwap.signal || '--'}</strong></div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px">
        Preço atual: ${fmtPrice(vwap.current)}<br>
        Desvio: ${vwap.deviation_pct?.toFixed(3) || '0'}%
      </div>
    </div>`;

    html += `</div>`;
    container.innerHTML = html;
    // Scroll down to detail
    container.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    container.innerHTML = `<div class="empty-state"><p style="color:var(--red)">Erro ao analisar ${asset}: ${e.message}</p></div>`;
  }
}

// =============================================
// CALCULADORA FINANCEIRA
// =============================================

async function loadFinance() {
  try {
    const res = await api('/finance/calculator', {}, 15000);
    const d = res.data || {};
    const s = d.summary || {};
    // KPIs
    document.getElementById('fin-capital').textContent = fmtMoney(s.capital);
    document.getElementById('fin-pnl-bruto').textContent = fmtMoney(s.pnl_bruto);
    document.getElementById('fin-pnl-bruto').className = `kpi-value ${s.pnl_bruto >= 0 ? 'green' : 'red'}`;
    document.getElementById('fin-fees-tax').textContent = fmtMoney(s.total_fees + s.total_tax_estimated);
    document.getElementById('fin-pnl-liq').textContent = fmtMoney(s.pnl_liquido);
    document.getElementById('fin-pnl-liq').className = `kpi-value ${s.pnl_liquido >= 0 ? 'green' : 'red'}`;
    document.getElementById('fin-rentab').textContent = `Rentab. líq.: ${s.rentabilidade_liquida_pct?.toFixed(2) || '0'}%`;
    // Fee rates
    const feeEl = document.getElementById('finance-fee-rates');
    const fr = d.fee_rates || {};
    feeEl.innerHTML = Object.entries(fr).map(([k, v]) => `<div class="config-item">
      <span class="config-key">${k.replace(/_/g, ' ')}</span>
      <span class="config-value">${(v * 100).toFixed(4)}%</span>
    </div>`).join('');
    // Tax rates
    const taxEl = document.getElementById('finance-tax-rates');
    const tr = d.tax_rates || {};
    const ex = d.tax_exemptions || {};
    let taxHtml = Object.entries(tr).map(([k, v]) => `<div class="config-item">
      <span class="config-key">${k.replace(/_/g, ' ')}</span>
      <span class="config-value">${(v * 100).toFixed(0)}%</span>
    </div>`).join('');
    if (ex.note) taxHtml += `<div style="margin-top:8px;padding:8px;background:var(--bg-tertiary);border-radius:6px;font-size:11px;color:var(--text-muted)">${ex.note}</div>`;
    taxEl.innerHTML = taxHtml;
    // Positions
    const tbody = document.getElementById('finance-positions-tbody');
    const positions = d.positions || [];
    if (!positions.length) {
      tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:24px;color:var(--text-muted)">Nenhuma posição ativa</td></tr>';
    } else {
      tbody.innerHTML = positions.map(p => `<tr>
        <td><strong>${p.asset}</strong></td>
        <td><span class="badge badge-${p.asset_type === 'crypto' ? 'yellow' : p.asset_type === 'b3' ? 'blue' : 'green'}">${p.asset_type}</span></td>
        <td>${p.quantity}</td>
        <td>${fmtPrice(p.entry_price)}</td>
        <td>${fmtMoney(p.allocated)}</td>
        <td class="${p.pnl_bruto >= 0 ? 'green' : 'red'}">${fmtMoney(p.pnl_bruto)}</td>
        <td>${p.fees_estimated?.toFixed(4) || '0'}</td>
        <td>${fmtMoney(p.tax_estimated)}</td>
        <td class="${p.pnl_liquido >= 0 ? 'green' : 'red'}">${fmtMoney(p.pnl_liquido)}</td>
        <td>${p.rentabilidade_pct?.toFixed(2) || '0'}%</td>
      </tr>`).join('');
    }
    document.getElementById('finance-update-time').textContent = `Atualizado: ${new Date().toLocaleTimeString()}`;
    setLastUpdate();
  } catch (e) {
    toast('Erro ao carregar calculadora financeira', 'error');
  }
}

// =============================================
// EVENTOS, CÂMBIO & DIVIDENDOS
// =============================================

async function loadEvents() {
  try {
    // Fetch all 3 endpoints in parallel
    const [evRes, fxRes, divRes] = await Promise.all([
      api('/market/events', {}, 30000).catch(() => null),
      api('/market/forex', {}, 15000).catch(() => null),
      api('/market/dividends', {}, 30000).catch(() => null),
    ]);

    // FOREX & INDICES
    const fxData = fxRes?.data || {};
    const currencies = fxData.currencies || {};
    const indices = fxData.indices || {};
    const fxCards = document.getElementById('forex-cards');
    let fxHtml = '';
    for (const [label, d] of Object.entries({...currencies, ...indices})) {
      const isUp = d.change_pct >= 0;
      fxHtml += `<div class="card" style="padding:10px;text-align:center">
        <div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">${label}</div>
        <div style="font-size:20px;font-weight:800">${d.price?.toLocaleString('pt-BR', {minimumFractionDigits: 2, maximumFractionDigits: 4})}</div>
        <div class="${isUp ? 'green' : 'red'}" style="font-size:13px;margin-top:2px">${isUp ? '▲' : '▼'} ${d.change_pct?.toFixed(2)}%</div>
      </div>`;
    }
    fxCards.innerHTML = fxHtml || '<div style="grid-column:1/-1;text-align:center;color:var(--text-muted);padding:12px">Dados de câmbio indisponíveis</div>';

    // CALENDAR
    const evData = evRes?.data || {};
    const calendar = evData.economic_calendar || [];
    const calTbody = document.getElementById('events-calendar-tbody');
    if (calendar.length) {
      calTbody.innerHTML = calendar.map(ev => {
        const impactClass = ev.impact === 'alto' ? 'red' : ev.impact === 'medio' ? 'yellow' : '';
        return `<tr>
          <td><strong>${ev.event}</strong></td>
          <td>${ev.region}</td>
          <td><span class="badge badge-${ev.impact === 'alto' ? 'red' : ev.impact === 'medio' ? 'yellow' : 'gray'}">${ev.impact}</span></td>
          <td style="font-size:12px">${ev.frequency}</td>
        </tr>`;
      }).join('');
    }

    // NEWS
    const news = evData.news || [];
    const newsEl = document.getElementById('events-news-list');
    if (news.length) {
      newsEl.innerHTML = news.map(n => `<div style="padding:8px 0;border-bottom:1px solid var(--border)">
        <div style="font-size:13px;color:var(--text-primary)">${n.title}</div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${n.source}</div>
      </div>`).join('');
    } else {
      newsEl.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:24px">Nenhuma notícia disponível</div>';
    }

    // DIVIDENDS
    const divData = divRes?.data || {};
    const divTbody = document.getElementById('events-dividends-tbody');
    const divEntries = Object.entries(divData);
    if (divEntries.length) {
      divTbody.innerHTML = divEntries.map(([asset, d], i) => `<tr>
        <td>${i + 1}</td>
        <td><strong>${asset}</strong></td>
        <td>${fmtPrice(d.price)}</td>
        <td>${d.annual_dividend?.toFixed(4) || '--'}</td>
        <td class="${d.dividend_yield_pct > 5 ? 'green' : ''}">${d.dividend_yield_pct?.toFixed(2) || '0'}%</td>
      </tr>`).join('');
    } else {
      divTbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--text-muted)">Sem dados de dividendos</td></tr>';
    }

    document.getElementById('events-update-time').textContent = `Atualizado: ${new Date().toLocaleTimeString()}`;
    setLastUpdate();
  } catch (e) {
    toast('Erro ao carregar eventos', 'error');
  }
}

// =============================================
// SEGURANÇA & COMPLIANCE
// =============================================

async function loadSecurity() {
  try {
    const [statusRes, auditRes] = await Promise.all([
      api('/security/status', {}, 15000).catch(() => null),
      api('/security/audit?limit=200', {}, 15000).catch(() => null),
    ]);

    // STATUS
    const d = statusRes?.data || {};
    const keys = d.api_keys || {};
    const prot = d.protections || {};
    const aud = d.audit || {};

    // KPIs
    const apiCount = Object.values(keys).filter(Boolean).length;
    document.getElementById('sec-apis').textContent = `${apiCount}/3`;
    document.getElementById('sec-apis').className = `kpi-value ${apiCount >= 2 ? 'green' : 'yellow'}`;

    const protCount = Object.values(prot).filter(p => p && p.active).length;
    document.getElementById('sec-protections').textContent = `${protCount}`;
    document.getElementById('sec-protections').className = `kpi-value ${protCount >= 4 ? 'green' : 'yellow'}`;

    document.getElementById('sec-audit-total').textContent = aud.total_events || 0;
    document.getElementById('sec-critical-24h').textContent = aud.critical_24h || 0;
    document.getElementById('sec-critical-24h').className = `kpi-value ${aud.critical_24h > 0 ? 'red' : 'green'}`;

    // API Keys detail
    const keyEl = document.getElementById('security-api-keys');
    keyEl.innerHTML = Object.entries(keys).map(([k, v]) => `<div class="config-item">
      <span class="config-key">${k.replace(/_/g, ' ')}</span>
      <span class="badge ${v ? 'badge-green' : 'badge-red'}">${v ? '✅ Configurada' : '❌ Não configurada'}</span>
    </div>`).join('');

    // Protections detail
    const protEl = document.getElementById('security-protections');
    protEl.innerHTML = Object.entries(prot).map(([k, v]) => `<div class="config-item">
      <span class="config-key">${k.replace(/_/g, ' ')}</span>
      <span class="config-value">${v?.active ? '✅ ' : '❌ '}${v?.value || ''}</span>
    </div>`).join('');

    // Compliance notes
    const compEl = document.getElementById('security-compliance');
    const notes = d.compliance_notes || [];
    compEl.innerHTML = notes.map(n => `<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:13px;color:var(--text-primary)">✅ ${n}</div>`).join('');

    // AUDIT LOG
    _renderAuditLog(auditRes?.data || []);

    document.getElementById('security-update-time').textContent = `Atualizado: ${new Date().toLocaleTimeString()}`;
    setLastUpdate();
  } catch (e) {
    toast('Erro ao carregar segurança', 'error');
  }
}

async function loadAuditLog() {
  const severity = document.getElementById('audit-severity-filter')?.value || '';
  try {
    const res = await api(`/security/audit?limit=200${severity ? '&severity=' + severity : ''}`, {}, 15000);
    _renderAuditLog(res.data || []);
  } catch (e) {
    toast('Erro ao carregar audit log', 'error');
  }
}

function _renderAuditLog(entries) {
  const tbody = document.getElementById('audit-log-tbody');
  if (!entries.length) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:24px;color:var(--text-muted)">Nenhum evento registrado</td></tr>';
    return;
  }
  tbody.innerHTML = entries.map(e => {
    const sevClass = e.severity === 'critical' ? 'badge-red' : e.severity === 'warning' ? 'badge-yellow' : 'badge-gray';
    const ts = e.timestamp ? new Date(e.timestamp).toLocaleString('pt-BR') : '--';
    const details = e.details ? JSON.stringify(e.details) : '--';
    return `<tr>
      <td style="font-size:12px;white-space:nowrap">${ts}</td>
      <td><span class="badge ${sevClass}">${e.severity}</span></td>
      <td style="font-size:12px">${e.action || '--'}</td>
      <td style="font-size:11px;color:var(--text-muted);max-width:200px;overflow:hidden;text-overflow:ellipsis">${details}</td>
    </tr>`;
  }).join('');
}

// =============================================
// TABS
// =============================================

function switchTab(el, paneId) {
  const container = el.closest('.tabs').parentElement;
  container.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  container.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  const pane = document.getElementById(paneId);
  if (pane) pane.classList.add('active');

  // Lazy load
  if (paneId === 'tab-trades') loadHistoryTrades();
  if (paneId === 'tab-dbstats') loadDbStats();
}
