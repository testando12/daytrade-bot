/* =============================================
   DAY TRADE BOT — App JavaScript
   SPA com roteamento, fetch API e Chart.js
   ============================================= */

'use strict';

// =============================================
// CONFIG
// =============================================

let API_BASE = 'http://localhost:8001';
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
    case 'live':      /* triggered manually */ break;
    case 'market':    loadMarketPage(); break;
    case 'trade':     loadTradePage(); break;
    case 'portfolio': loadPortfolio(); break;
    case 'risk':      loadRiskPage(); break;
    case 'history':   loadHistoryPage(); break;
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

async function api(path, options = {}, timeoutMs = 12000) {
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
    await api('/');
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
  const icons = { success: '✅', error: '❌', info: 'ℹ️', warning: '⚠️' };
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

// =============================================
// PAGE: DASHBOARD
// =============================================

async function loadDashboard() {
  try {
    setApiStatus(true);
    // Parallel: full analysis + market prices + risk status
    const [analysis, riskStatus] = await Promise.all([
      api('/analyze/full', { method: 'POST' }).catch(() => null),
      api('/risk/status').catch(() => null),
    ]);

    if (analysis && analysis.success) {
      renderDashboardKPIs(analysis.data, riskStatus);
      renderMomentumChart(analysis.data);
      renderRiskRadar(analysis.data);
      renderDashboardAllocation(analysis.data);
    }

    // Prices separately (may take longer)
    loadMarketPrices();
    setLastUpdate();
  } catch (e) {
    setApiStatus(false);
    toast('Erro ao conectar com a API', 'error');
  }
}

function renderDashboardKPIs(data, riskStatus) {
  // Capital
  const capital = data.market_data?.capital || data.portfolio?.capital || 150;
  document.getElementById('kpi-capital').textContent = fmtMoney(capital);

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
    const data = await api('/market/analyze-live', { method: 'POST' });
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
    const data = await api('/analyze/full', { method: 'POST' });
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
  const grid = document.getElementById('market-assets-grid');
  const tbody = document.getElementById('market-table-body');
  const timeEl = document.getElementById('market-update-time');

  grid.innerHTML = '<div class="loading-overlay" style="grid-column:1/-1"><div class="spinner"></div> Carregando...</div>';
  tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--text-muted)">Carregando...</td></tr>';

  try {
    const [pricesRes, analysisRes] = await Promise.all([
      api('/market/prices').catch(() => null),
      api('/analyze/full', { method: 'POST' }).catch(() => null),
    ]);

    const prices = pricesRes?.data || {};
    const mom = analysisRes?.data?.momentum_analysis || {};

    // Asset cards
    let cardsHtml = '';
    for (const [asset, price] of Object.entries(prices)) {
      const m = mom[asset];
      const score = m?.momentum_score;
      const cls = m?.classification || 'LATERAL';
      const up = score != null && score >= 0;
      cardsHtml += `
        <div class="asset-card">
          <div class="asset-header">
            <span class="asset-symbol">${asset}</span>
            ${classifBadge(cls)}
          </div>
          <div class="asset-price">${fmtPrice(price)}</div>
          ${score != null ? `<div class="asset-change ${up ? 'up' : 'down'}">${up ? '▲' : '▼'} Score: ${score.toFixed(4)}</div>` : ''}
        </div>`;
    }
    grid.innerHTML = cardsHtml || '<div class="empty-state" style="grid-column:1/-1"><p>Sem dados</p></div>';

    // Table
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
    timeEl.textContent = `Atualizado: ${new Date().toLocaleTimeString('pt-BR')}`;

  } catch (e) {
    grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="empty-icon">📡</div><p>Erro ao carregar dados</p></div>';
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
    const data = await api('/analyze/full', { method: 'POST' });
    if (!data.success) throw new Error();

    const allocs = data.data.allocations || {};
    const mom = data.data.momentum_analysis || {};
    const irq = data.data.risk_analysis?.irq_score || 0;

    let total = 0;
    const entries = Object.entries(allocs);
    for (const [, d] of entries) total += d.recommended_amount || 0;

    totalEl.textContent = `Total: ${fmtMoney(total)}`;

    // Pie chart
    destroyChart('portfolio-pie');
    const pieCtx = document.getElementById('chart-portfolio-pie');
    const nonZero = entries.filter(([, d]) => d.recommended_amount > 0);
    const pieColors = ['#388bfd','#3fb950','#d29922','#f85149','#db6d28','#8b949e','#a5d6ff','#7ee787'];

    if (nonZero.length > 0 && pieCtx) {
      charts['portfolio-pie'] = new Chart(pieCtx, {
        type: 'doughnut',
        data: {
          labels: nonZero.map(([a]) => a),
          datasets: [{
            data: nonZero.map(([, d]) => d.recommended_amount),
            backgroundColor: pieColors.slice(0, nonZero.length),
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
      pieCtx.parentElement.innerHTML = '<div class="empty-state"><div class="empty-icon">💼</div><p>Sem alocações positivas no momento</p></div>';
    }

    // Summary
    let summaryHtml = '';
    const holdCount = entries.filter(([, d]) => d.action === 'HOLD').length;
    const buyCount = entries.filter(([, d]) => d.action === 'BUY').length;
    const sellCount = entries.filter(([, d]) => d.action === 'SELL').length;
    summaryHtml += `
      <div class="config-item"><span class="config-key">Capital Total Alocado</span><span class="config-value">${fmtMoney(total)}</span></div>
      <div class="config-item"><span class="config-key">IRQ Score Atual</span><span class="config-value text-${irq < 0.6 ? 'green' : 'red'}">${(irq*100).toFixed(1)}%</span></div>
      <div class="config-item"><span class="config-key">Ativos em HOLD</span><span class="config-value">${holdCount}</span></div>
      <div class="config-item"><span class="config-key">Ativos em BUY</span><span class="config-value text-green">${buyCount}</span></div>
      <div class="config-item"><span class="config-key">Ativos em SELL</span><span class="config-value text-red">${sellCount}</span></div>
    `;
    summaryEl.innerHTML = summaryHtml;

    // Table
    let rows = '';
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
    tbodyEl.innerHTML = rows || '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--text-muted)">Sem dados</td></tr>';
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
    const [riskStatus, canTradeData, analysisData] = await Promise.all([
      api('/risk/status'),
      api('/risk/can-trade'),
      api('/analyze/full', { method: 'POST' }).catch(() => null),
    ]);

    const rs = riskStatus.data;
    const ct = canTradeData.data;
    const irq = analysisData?.data?.risk_analysis;

    // KPIs
    const canTrade = ct.allowed;
    const kpiCanEl = document.getElementById('risk-can-trade');
    kpiCanEl.textContent = canTrade ? '✅ SIM' : '🔒 NÃO';
    kpiCanEl.style.fontSize = '20px';
    document.getElementById('risk-can-trade-reason').textContent = ct.reason || '--';

    const pnl = rs.daily_pnl || 0;
    const pnlEl = document.getElementById('risk-daily-pnl');
    pnlEl.textContent = fmtMoney(pnl);
    pnlEl.className = `kpi-value ${pnl >= 0 ? 'text-green' : 'text-red'}`;
    document.getElementById('risk-daily-pnl-label').textContent = `Perda máx: ${fmtMoney(rs.daily_loss_limit?.max_loss)}`;

    document.getElementById('risk-trades-hour').textContent =
      `${rs.trade_limits?.trades_last_hour || 0} / ${ct.trade_limits?.remaining_hour != null ? (ct.trade_limits.remaining_hour + (rs.trade_limits?.trades_last_hour||0)) : 20}`;
    document.getElementById('risk-trades-day').textContent =
      `${rs.trade_limits?.trades_today || 0} / ${ct.trade_limits?.remaining_day != null ? (ct.trade_limits.remaining_day + (rs.trade_limits?.trades_today||0)) : 100}`;

    // Config items
    const configEl = document.getElementById('risk-config-items');
    configEl.innerHTML = `
      <div class="config-item"><span class="config-key">Stop Loss</span><span class="config-value text-red">${rs.stop_loss_pct || 5}%</span></div>
      <div class="config-item"><span class="config-key">Take Profit</span><span class="config-value text-green">${rs.take_profit_pct || 10}%</span></div>
      <div class="config-item"><span class="config-key">Limite Diário de Perda</span><span class="config-value">${fmtMoney(rs.daily_loss_limit?.max_loss)}</span></div>
      <div class="config-item"><span class="config-key">Trades Últimas 1h</span><span class="config-value">${rs.trade_limits?.trades_last_hour || 0}</span></div>
      <div class="config-item"><span class="config-key">Trades Hoje</span><span class="config-value">${rs.trade_limits?.trades_today || 0}</span></div>
      <div class="config-item"><span class="config-key">Posições Abertas</span><span class="config-value">${rs.open_positions || 0}</span></div>
    `;

    // IRQ Signals
    const lockBadge = document.getElementById('risk-lock-badge');
    lockBadge.textContent = rs.is_locked ? '🔒 BLOQUEADO' : '🟢 OK';
    lockBadge.className = `badge ${rs.is_locked ? 'badge-red' : 'badge-green'}`;

    const irqBadgeEl = document.getElementById('risk-irq-badge-page');
    if (irq) {
      const level = irq.level || irq.protection_level;
      irqBadgeEl.textContent = `${irq.color} ${level}`;
      irqBadgeEl.className = `badge ${irqBadgeClass(level)}`;
      document.getElementById('risk-signals-list').innerHTML = renderSignalsInline(irq);
    } else {
      document.getElementById('risk-signals-list').innerHTML = '<div class="loading-overlay">Análise não disponível</div>';
    }

    // Limits progress
    const limits = rs.daily_loss_limit;
    const trades = rs.trade_limits;
    const dailyUsed = limits ? Math.abs(limits.daily_pnl / limits.max_loss) * 100 : 0;
    const tradeHourUsed = trades ? (trades.trades_last_hour / 20) * 100 : 0;
    const tradeDayUsed = trades ? (trades.trades_today / 100) * 100 : 0;

    document.getElementById('risk-limits-section').innerHTML = `
      <div style="margin-bottom:16px">
        <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:6px">
          <span class="text-muted">Perda Diária Usada</span>
          <span>${fmtMoney(pnl)} / ${fmtMoney(limits?.max_loss)}</span>
        </div>
        <div class="progress-bar" style="height:10px">
          <div class="progress-fill ${dailyUsed > 80 ? 'red' : dailyUsed > 50 ? 'yellow' : 'green'}" style="width:${Math.min(dailyUsed,100)}%"></div>
        </div>
      </div>
      <div style="margin-bottom:16px">
        <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:6px">
          <span class="text-muted">Trades Últimas 1h</span>
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
    const res = await api('/trade/status');
    if (!res.success) return;
    const d = res.data;

    // Status bar
    const isActive = d.auto_trading;
    const badge = document.getElementById('trade-bot-badge');
    if (badge) {
      badge.textContent = isActive ? '🟢 ATIVO' : '⏸ PARADO';
      badge.className = `badge ${isActive ? 'badge-green' : 'badge-gray'}`;
    }
    const toggleBtn = document.getElementById('trade-toggle-btn');
    if (toggleBtn) {
      toggleBtn.textContent = isActive ? '⏸ Pausar Bot' : '▶ Iniciar Bot';
      toggleBtn.className = `btn ${isActive ? 'btn-secondary' : 'btn-primary'}`;
    }
    const capitalBadge = document.getElementById('trade-capital-badge');
    if (capitalBadge) capitalBadge.textContent = fmtMoney(d.capital);

    const lastCycle = document.getElementById('trade-last-cycle');
    if (lastCycle) {
      lastCycle.textContent = d.last_cycle
        ? new Date(d.last_cycle).toLocaleString('pt-BR')
        : '—';
    }

    // Capital card
    const capEl = document.getElementById('trade-current-capital');
    if (capEl) capEl.textContent = fmtMoney(d.capital);
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
    'SISTEMA':   { cls: 'badge-gray',   icon: '⚙️' },
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
