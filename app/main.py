"""
Aplicação FastAPI principal - Day Trade Bot
"""

# Carrega variáveis do arquivo .env antes de qualquer import de configuração
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from contextlib import asynccontextmanager
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Depends, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from datetime import datetime
from pathlib import Path
import asyncio
import json
import random as _rnd
import os
import hashlib
import secrets
import time

from app.core.config import settings
from app import db_state
from app.engines import MomentumAnalyzer, RiskAnalyzer, PortfolioManager
from app.engines.mean_reversion import MeanReversionAnalyzer
from app.engines.breakout import BreakoutAnalyzer
from app.engines.squeeze import SqueezeAnalyzer
from app.engines.liquidity_sweep import LiquiditySweepAnalyzer
from app.engines.fvg import FVGAnalyzer
from app.engines.regime import RegimeDetector
from app.engines.vwap_reversion import VWAPReversionAnalyzer
from app.engines.pyramid_breakout import PyramidBreakoutAnalyzer
from app.engines.risk_manager import risk_manager

# Database
try:
    from app.core.database import db
    DB_AVAILABLE = db is not None
except Exception:
    db = None
    DB_AVAILABLE = False

# Market Data (Binance)
try:
    from app.market_data import market_data_service, MARKET_DATA_AVAILABLE
except ImportError:
    market_data_service = None
    MARKET_DATA_AVAILABLE = False

# Imports opcionais: falham graciosamente se dependências não instaladas
try:
    from app.alerts import alert_manager, AlertLevel
    ALERTS_AVAILABLE = True
except ImportError:
    alert_manager = None
    AlertLevel = None
    ALERTS_AVAILABLE = False

try:
    from app.ml_predictor import PricePredictorML, MLEnsemble
    ML_AVAILABLE = True
except ImportError:
    MLEnsemble = None
    ML_AVAILABLE = False

# ═══════════════════════════════════════════
# SEGURANÇA — API KEY AUTHENTICATION
# ═══════════════════════════════════════════

# Gera uma API key aleatória se nenhuma foi configurada (exibe no log na primeira vez)
_API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")
if not _API_SECRET_KEY:
    _API_SECRET_KEY = secrets.token_urlsafe(32)
    print(f"[SECURITY] ⚠️  Nenhuma API_SECRET_KEY configurada. Chave temporária gerada:", flush=True)
    print(f"[SECURITY] 🔑 {_API_SECRET_KEY}", flush=True)
    print(f"[SECURITY] ➡️  Configure API_SECRET_KEY nas env vars do Railway para persistir.", flush=True)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Endpoints que NÃO precisam de autenticação (públicos)
_PUBLIC_ENDPOINTS = {
    "/", "/health", "/diagnostics", "/docs", "/openapi.json", "/redoc",
    "/ui", "/dashboard", "/simulador", "/backup-state",
}
# Prefixos públicos (static files, etc)
_PUBLIC_PREFIXES = ("/ui/",)

def _is_public_endpoint(path: str) -> bool:
    """Verifica se um endpoint é público (não requer auth)."""
    if path in _PUBLIC_ENDPOINTS:
        return True
    for prefix in _PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return True
    return False

async def verify_api_key(request: Request, api_key: str = Security(_api_key_header)):
    """Dependency de autenticação — valida X-API-Key header."""
    if _is_public_endpoint(request.url.path):
        return None  # endpoints públicos não precisam de auth
    if not api_key:
        raise HTTPException(status_code=401, detail="API key ausente. Envie header X-API-Key.")
    if not secrets.compare_digest(api_key, _API_SECRET_KEY):
        raise HTTPException(status_code=403, detail="API key inválida.")
    return api_key


# ═══════════════════════════════════════════
# SEGURANÇA — RATE LIMITING
# ═══════════════════════════════════════════

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    _limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])
    RATE_LIMIT_AVAILABLE = True
except ImportError:
    _limiter = None
    RATE_LIMIT_AVAILABLE = False
    print("[SECURITY] ⚠️  slowapi não instalado — rate limiting desativado. pip install slowapi", flush=True)


# ═══════════════════════════════════════════
# SEGURANÇA — SECURITY HEADERS MIDDLEWARE
# ═══════════════════════════════════════════

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adiciona headers de segurança em todas as respostas."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # CSP: permite inline scripts/styles para o dashboard + CDN do Chart.js + Google Fonts + Font Awesome
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
            "img-src 'self' data:; "
            "connect-src 'self' https://*.up.railway.app http://localhost:*; "
            "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
            "frame-ancestors 'none';"
        )
        # Evita cache de todos os arquivos do dashboard (para que deploy novo apareça imediatamente)
        path = request.url.path
        if path.startswith('/ui') or path.endswith(('.html', '.js', '.css')):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


# ═══════════════════════════════════════════
# SCHEDULER AUTOMÁTICO
# ═══════════════════════════════════════════

_last_scheduler_errors: list = []  # últimos 10 erros do scheduler
_scheduler_debug = {"step": "init", "ts": "", "loop_count": 0}  # debug tracker
_consecutive_errors: int = 0  # erros consecutivos do scheduler (global para /diagnostics)

_scheduler_state = {
    "running": False,
    "interval_minutes": 30,   # ciclo a cada 30 minutos (B3); mín 60min fora do B3
    "only_market_hours": True, # mantido por compatibilidade; crypto sempre roda
    "next_run": None,
    "total_auto_cycles": 0,
    "task": None,
    "session": "",             # sessão atual: "B3+Crypto" ou "Crypto24/7"
}

_SCHEDULER_PERSIST_KEYS = ("interval_minutes", "only_market_hours", "next_run", "total_auto_cycles", "session")


def _persist_scheduler_state():
    global _consecutive_errors
    try:
        payload = {k: _scheduler_state.get(k) for k in _SCHEDULER_PERSIST_KEYS}
        payload["consecutive_errors"] = _consecutive_errors  # persiste entre restarts
        db_state.save_state("scheduler_state", payload)
    except Exception:
        pass


def _effective_total_cycles() -> int:
    """
    Retorna o total real de ciclos executados.
    Prioridade: win_count + loss_count (sempre cumulativo e correto)
    mesmo que o array cycles[] esteja vazio (ex: após reinício/migração).
    """
    if not isinstance(_perf_state, dict):
        return 0
    wins   = int(_perf_state.get("win_count", 0) or 0)
    losses = int(_perf_state.get("loss_count", 0) or 0)
    return wins + losses

# Controle de reinvestimento diário
_last_reinvestment_date: str = ""  # data da última vez que reinvestiu (YYYY-MM-DD)
_last_daily_summary_date: str = ""  # data do último resumo diário enviado

# Alocação por timeframe: SHORT 2% + MEDIUM 28% + LONG 28% + MR 8% + BO 6% + SQ 5% + LS 2% + FVG 2% + VR 10% + PB 9% = 100%
# v5.0 (otimização capital baixo): 1h gera 85% do lucro → boost 20%→28%, 5m drenou → 8%→2%, 1d 30%→28%
# VR excela em lateral (ADX<20), PB excela em tendência (ADX>25) — sistema complementar
# 1d é o motor de lucro (R$47 vs R$2 do 1h e R$0.6 do 5m) → boost 1d, reduz 5m
_TIMEFRAME_ALLOC   = {"5m": 0.01, "1h": 0.25, "1d": 0.40}
_MR_ALLOC_PCT      = 0.08   # capital dedicado ao bucket de Mean Reversion
_BO_ALLOC_PCT      = 0.06   # capital dedicado ao bucket de Breakout
_SQ_ALLOC_PCT      = 0.05   # capital dedicado ao bucket de Squeeze (Volatility Compression)
_LS_ALLOC_PCT      = 0.02   # capital dedicado ao bucket de Liquidity Sweep (Stop Hunt)
_FVG_ALLOC_PCT     = 0.02   # capital dedicado ao bucket de Fair Value Gap (Imbalance Fill)
_VR_ALLOC_PCT      = 0.10   # capital dedicado ao bucket de VWAP Reversion (novo v4.0)
_PB_ALLOC_PCT      = 0.09   # capital dedicado ao bucket de Pyramid Breakout (novo v4.0)
# v2.1 (2026-03-05): top-N reduzido para 1 por bucket — operar só o melhor sinal de cada timeframe
_TIMEFRAME_N_ASSETS = {"5m": 1, "1h": 1, "1d": 1}  # era 1/2/3 — com 1d=3 perdia em dias de queda

# Estratégia adaptativa (paper-first): alocação dinâmica + filtro de regime + risco por timeframe
_strategy_state = {
    "dynamic_alloc_enabled": True,
    "regime_filter_enabled": True,
    "tf_risk_tuning_enabled": True,
    "alloc_dominance_threshold": 0.75,
    "alloc_lookback_cycles": 60,
    "min_cycles_for_realloc": 20,
    "high_irq_threshold": 0.12,
    "extreme_irq_threshold": 0.18,
    "tf_sl_mult": {"5m": 0.75, "1h": 1.0, "1d": 1.20},
    "tf_tp_mult": {"5m": 0.80, "1h": 1.0, "1d": 1.25},
    "regime": "normal",
    "last_effective_alloc": dict(_TIMEFRAME_ALLOC),
    "last_reason": "base",
    "last_recent_tf_pnl": {"5m": 0.0, "1h": 0.0, "1d": 0.0},
}

# ─────────────────────────────────────────────────────────────────────────────
# SIMULATION STRESS MODE — controla o quão pessimista é a simulação paper
# normal  : dia médio de mercado (calibrado para Binance + B3 mid-cap)
# stress  : dia ruim (alta vol, spreads alargados, book fino)
# extreme : crise / black swan (evento macro, halting, flash crash parcial)
# Altere em runtime via POST /trade/sim-mode {"mode": "stress"}
# ─────────────────────────────────────────────────────────────────────────────
_SIM_PROFILES = {
    "normal": {
        "rejection_rate":    0.08,    # % ordens não preenchidas
        "capture_mean":      0.85,    # adverse selection: captura 85% (era 80%) — R:R boost
        "capture_std":       0.10,    # menos variação na captura (era 0.12)
        "loss_worsen_mean":  1.05,    # perde X% a mais em stops
        "loss_worsen_std":   0.08,
        "noise_mean":       -0.0001,  # ruído de execução
        "noise_std":         0.0010,
        "cost_multiplier":   1.00,    # multiplicador sobre spread/slippage
        "min_ret_threshold": 0.0018,  # filtra retornos abaixo dos custos
    },
    "stress": {
        "rejection_rate":    0.20,    # 20% das ordens rejeitadas/não preenchidas
        "capture_mean":      0.65,    # captura apenas 65% do movimento
        "capture_std":       0.15,
        "loss_worsen_mean":  1.15,    # perde 15% a mais em stops
        "loss_worsen_std":   0.12,
        "noise_mean":       -0.0005,
        "noise_std":         0.0030,
        "cost_multiplier":   2.50,    # spreads 2.5× maiores
        "min_ret_threshold": 0.0035,  # threshold maior (custos maiores)
    },
    "extreme": {
        "rejection_rate":    0.35,    # 35% das ordens — mercado trava
        "capture_mean":      0.45,    # captura apenas 45% do movimento
        "capture_std":       0.20,
        "loss_worsen_mean":  1.35,    # perde 35% a mais em stops (gap down)
        "loss_worsen_std":   0.18,
        "noise_mean":       -0.0015,
        "noise_std":         0.0070,
        "cost_multiplier":   5.00,    # spreads 5× (flash crash / evento macro)
        "min_ret_threshold": 0.0060,
    },
}
_sim_mode: str = os.getenv("SIM_MODE", "extreme")   # padrão extreme — alterável via /trade/sim-mode ou env SIM_MODE


# Modelo de custos (simulação realista paper-first)
# v2 (2026-03-04): B3 slippage 2→10 bps e spread 3→5 bps (realista para mid-cap Bovespa)
# Referência: mid-caps B3 têm slippage de 8–15 bps em condições normais de book
_TRADING_COST_MODEL = {
    "b3":        {"brokerage_bps": 2.0,  "exchange_bps": 1.8, "spread_bps": 5.0, "slippage_bps": 10.0, "fx_bps": 0.0,  "min_fee_brl": 0.05},
    "us":        {"brokerage_bps": 2.5,  "exchange_bps": 0.8, "spread_bps": 4.0, "slippage_bps": 2.5,  "fx_bps": 35.0, "min_fee_brl": 0.10},
    "crypto":    {"brokerage_bps": 10.0, "exchange_bps": 0.0, "spread_bps": 3.0, "slippage_bps": 5.0,  "fx_bps": 0.0,  "min_fee_brl": 0.05},
    "forex":     {"brokerage_bps": 0.0,  "exchange_bps": 0.0, "spread_bps": 8.0, "slippage_bps": 3.0,  "fx_bps": 0.0,  "min_fee_brl": 0.05},
    "commodity": {"brokerage_bps": 2.0,  "exchange_bps": 1.0, "spread_bps": 6.0, "slippage_bps": 3.0,  "fx_bps": 20.0, "min_fee_brl": 0.08},
    "other":     {"brokerage_bps": 2.0,  "exchange_bps": 1.0, "spread_bps": 5.0, "slippage_bps": 3.0,  "fx_bps": 0.0,  "min_fee_brl": 0.05},
}


def _asset_market(asset: str) -> str:
    a = (asset or "").upper()
    if a in settings.ALLOWED_ASSETS:
        return "b3"
    if a in settings.US_STOCKS:
        return "us"
    if a in settings.CRYPTO_ASSETS:
        return "crypto"
    if a in settings.FOREX_PAIRS:
        return "forex"
    if a in settings.COMMODITIES:
        return "commodity"
    return "other"


def _estimate_trade_costs_brl(asset: str, notional_brl: float, abs_return: float = 0.0) -> dict:
    market = _asset_market(asset)
    model = _TRADING_COST_MODEL.get(market, _TRADING_COST_MODEL["other"])
    cost_mult = _SIM_PROFILES.get(_sim_mode, _SIM_PROFILES["normal"])["cost_multiplier"]

    # Slippage cresce com volatilidade do candle (limitado) + multiplicador do modo stress
    vol_factor = 1.0 + min(max(abs_return, 0.0), 0.05) * 8.0
    slippage_bps_eff = float(model["slippage_bps"]) * vol_factor * cost_mult

    brokerage = notional_brl * ((float(model["brokerage_bps"]) * 2.0) / 10000.0)
    exchange_fees = notional_brl * ((float(model["exchange_bps"]) * 2.0) / 10000.0)
    spread = notional_brl * (float(model["spread_bps"]) * cost_mult / 10000.0)
    slippage = notional_brl * (slippage_bps_eff / 10000.0)
    fx = notional_brl * (float(model["fx_bps"]) / 10000.0)

    total = brokerage + exchange_fees + spread + slippage + fx
    min_fee = float(model.get("min_fee_brl", 0.0))
    min_fee_adj = 0.0
    if total < min_fee:
        min_fee_adj = min_fee - total
        total = min_fee

    return {
        "market": market,
        "brokerage": round(brokerage, 6),
        "exchange_fees": round(exchange_fees, 6),
        "spread": round(spread, 6),
        "slippage": round(slippage, 6),
        "fx": round(fx, 6),
        "min_fee_adj": round(min_fee_adj, 6),
        "total": round(total, 6),
    }


def _normalize_alloc(alloc: dict) -> dict:
    total = sum(max(0.0, float(v)) for v in alloc.values())
    if total <= 0:
        return dict(_TIMEFRAME_ALLOC)
    return {k: max(0.0, float(v)) / total for k, v in alloc.items()}


def _dynamic_strategy_weights(lookback: int = 30) -> dict:
    """
    Dynamic Strategy Allocation v1 — ajusta capital de cada bucket pelo Sharpe recente.
    Lê os últimos `lookback` ciclos e calcula: mean(pnl) / std(pnl) por estratégia.
    Sharpe > 1.0 → ×1.50 | > 0.5 → ×1.25 | > 0 → ×1.10 | > -0.3 → ×0.85 | else → ×0.60
    """
    cycles = _perf_state.get("cycles", [])[-lookback:]
    if len(cycles) < 5:
        return {k: 1.0 for k in ("mr", "bo", "sq", "ls", "fvg", "vr", "pb")}

    def _sharpe(key: str) -> float:
        vals = [c.get(key, 0.0) or 0.0 for c in cycles]
        if len(vals) < 2:
            return 0.0
        mu   = sum(vals) / len(vals)
        std  = (sum((x - mu) ** 2 for x in vals) / len(vals)) ** 0.5
        return (mu / std) if std > 0 else 0.0

    def _sharpe_to_mult(s: float) -> float:
        if s >  1.0: return 1.50
        if s >  0.5: return 1.25
        if s >  0.0: return 1.10
        if s > -0.3: return 0.85
        return 0.60

    return {
        k: round(_sharpe_to_mult(_sharpe(f"pnl_{k}")), 3)
        for k in ("mr", "bo", "sq", "ls", "fvg", "vr", "pb")
    }


def _calc_adx_simple(prices: list, period: int = 14) -> float:
    """
    ADX simplificado usando apenas fechamentos (sem High/Low).
    Mantido para compatibilidade; internamente o RegimeDetector usa a mesma lógica.
    Retorna valor 0-100; >25 = tendência forte, <18 = mercado lateral.
    """
    if len(prices) < period * 3:
        return 20.0  # neutro por padrão
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    recent  = changes[-(period * 3):]

    def wilder(vals: list, p: int) -> float:
        if len(vals) < p:
            return sum(abs(v) for v in vals) / max(len(vals), 1)
        sm = sum(abs(v) for v in vals[:p])
        for v in vals[p:]:
            sm = sm - sm / p + abs(v)
        return sm

    plus_dm  = [max(c, 0.0) for c in recent]
    minus_dm = [max(-c, 0.0) for c in recent]
    tr_vals  = [abs(c) for c in recent]

    sm_plus  = wilder(plus_dm, period)
    sm_minus = wilder(minus_dm, period)
    sm_tr    = wilder(tr_vals, period)

    if sm_tr == 0:
        return 0.0
    plus_di  = 100.0 * sm_plus  / sm_tr
    minus_di = 100.0 * sm_minus / sm_tr
    denom    = plus_di + minus_di
    if denom == 0:
        return 0.0
    dx = 100.0 * abs(plus_di - minus_di) / denom
    # Suaviza com Wilder sobre as últimas 'period' estimativas de DX
    return round(dx, 2)


def _effective_timeframe_alloc(irq_score: float) -> tuple:
    """Retorna alocação efetiva por timeframe com base em dominância + regime."""
    alloc = dict(_TIMEFRAME_ALLOC)
    reasons = []
    regime = "normal"

    recent = _perf_state.get("cycles", [])[-int(_strategy_state.get("alloc_lookback_cycles", 60)):]
    tf_recent = {
        "5m": round(sum((c.get("pnl_5m", 0) or 0) for c in recent), 4),
        "1h": round(sum((c.get("pnl_1h", 0) or 0) for c in recent), 4),
        "1d": round(sum((c.get("pnl_1d", 0) or 0) for c in recent), 4),
    }

    if _strategy_state.get("dynamic_alloc_enabled", True) and len(recent) >= int(_strategy_state.get("min_cycles_for_realloc", 20)):
        total_abs = abs(tf_recent["5m"]) + abs(tf_recent["1h"]) + abs(tf_recent["1d"])
        dom_1d = (abs(tf_recent["1d"]) / total_abs) if total_abs > 0 else 0.0
        if tf_recent["1d"] > 0 and dom_1d >= float(_strategy_state.get("alloc_dominance_threshold", 0.75)):
            alloc = {"5m": 0.02, "1h": 0.13, "1d": 0.85}  # v5.1: 1h reduzido (negativo), 1d boosted
            reasons.append(f"1d dominante ({dom_1d*100:.1f}%)")
        elif (tf_recent["5m"] + tf_recent["1h"]) > max(tf_recent["1d"], 0):
            alloc = {"5m": 0.05, "1h": 0.45, "1d": 0.50}
            reasons.append("curto prazo dominante — 1h boost")

    if _strategy_state.get("regime_filter_enabled", True):
        high_irq = float(_strategy_state.get("high_irq_threshold", 0.12))
        extreme_irq = float(_strategy_state.get("extreme_irq_threshold", 0.18))
        loss_streak = int(_protection_state.get("consecutive_losses", 0))

        if irq_score >= extreme_irq or loss_streak >= 4:
            regime = "extreme"
            # v2.1: Mercado turbulento/perdas — proteger capital reduzindo 1d (mais exposto)
            # Era: 1d×1.35 (ERRADO — aumentava exposição no pior momento)
            alloc = {
                "5m": alloc["5m"] * 0.50,   # manter algum scalping
                "1h": alloc["1h"] * 0.30,   # reduzir 1h
                "1d": alloc["1d"] * 0.10,   # quase zerar 1d em queda extrema
            }
            reasons.append("regime extremo: protege capital, reduz 1d drasticamente")
        elif irq_score >= high_irq or loss_streak >= 2:
            regime = "cautious"
            # v2.1: Sinal de alerta — reduzir 1d moderadamente
            # Era: 1d×1.15 (ERRADO — reforçava a perda)
            alloc = {
                "5m": alloc["5m"] * 0.70,
                "1h": alloc["1h"] * 0.60,
                "1d": alloc["1d"] * 0.50,   # metade da exposição 1d em regime cauteloso
            }
            reasons.append("regime cauteloso: reduz exposição 1d")

    alloc = _normalize_alloc(alloc)
    _strategy_state["regime"] = regime
    _strategy_state["last_effective_alloc"] = alloc
    _strategy_state["last_reason"] = " | ".join(reasons) if reasons else "base"
    _strategy_state["last_recent_tf_pnl"] = tf_recent

    return alloc, regime, _strategy_state["last_reason"], tf_recent

def _is_market_open() -> bool:
    """Verifica se o mercado B3 está aberto (seg-sex 10:00-17:00 BRT = UTC-3)."""
    from datetime import timezone, timedelta
    brt = timezone(timedelta(hours=-3))
    now = datetime.now(brt)
    if now.weekday() >= 5:   # sábado=5, domingo=6
        return False
    return 10 <= now.hour < 17


def _current_session() -> tuple:
    """
    Retorna (assets, session_label) de acordo com o horário BRT:
    - B3 aberta (seg-sex 10-17h)          → B3 + US + Global (todos os ativos)
    - NYSE aberta fora B3 (13h30-20h)     → US + ETFs Int'l + Crypto + Commodities
    - Europa aberta (05h-10h BRT)         → ETFs Int'l + Forex + Crypto + Commodities
    - Fora de horário  (20h-05h BRT)      → Crypto + Commodities agro (CME 23h/dia)
    """
    from datetime import timezone, timedelta
    brt = timezone(timedelta(hours=-3))
    now = datetime.now(brt)
    weekday = now.weekday()  # 0=seg .. 4=sex
    hour    = now.hour
    minute  = now.minute

    b3_open   = weekday < 5 and 10 <= hour < 17
    # NYSE abre 9h30 EST = 13h30 BRT
    nyse_open = weekday < 5 and (hour > 13 or (hour == 13 and minute >= 30)) and hour < 20
    # Bolsas europeias: Frankfurt/Londres abrem ~09h/10h CET = 05h/06h BRT
    europe_open = weekday < 5 and 5 <= hour < 10

    _intl_etfs  = getattr(settings, "INTL_ETFS", [])
    _commodities = settings.COMMODITIES
    _forex       = settings.FOREX_PAIRS

    if b3_open:
        total = len(settings.ALL_ASSETS)
        return settings.ALL_ASSETS, f"[BR] B3 + [US] US + [GLOBAL] ({total} ativos)"
    elif nyse_open:
        # NYSE: US stocks + ETFs internacionais + Crypto + Commodities
        us_global = settings.US_STOCKS + _intl_etfs + settings.CRYPTO_ASSETS + _commodities
        return us_global, f"[US] NYSE + [GLOBAL] ETFs Int'l + [CRYPTO] ({len(us_global)} ativos)"
    elif europe_open:
        # Europa: ETFs internacionais + Forex + Crypto + Commodities
        eu_session = _intl_etfs + _forex + settings.CRYPTO_ASSETS + _commodities
        return eu_session, f"[EU] Europa + Forex + [CRYPTO] ({len(eu_session)} ativos)"
    else:
        # Noite / Ásia: Crypto + Commodities agro (CME Globex cobre madrugada)
        overnight = settings.CRYPTO_ASSETS + _commodities
        return overnight, f"[CRYPTO+AGRO] Noite/Ásia ({len(overnight)} ativos)"


def _align_to_candle_close(min_wait_sec: int, candle_minutes: int = 5) -> int:
    """Retorna segundos até o próximo fechamento de candle >= min_wait_sec.
    Assim o bot sempre opera sobre candles completos (nunca candle incompleto).
    Exemplo: ciclo de 10min, candle 5m → dorme até o próximo fechamento 5m que
    esteja a pelo menos 10min de distância.
    """
    candle_sec = candle_minutes * 60
    now = datetime.now()
    elapsed_in_candle = (now.minute * 60 + now.second) % candle_sec
    to_next_close = candle_sec - elapsed_in_candle
    # Avança para o próximo fechamento que respeita o intervalo mínimo
    while to_next_close < min_wait_sec:
        to_next_close += candle_sec
    return to_next_close


async def _auto_cycle_loop():
    """Loop interno do scheduler: executa ciclos de trading automaticamente."""
    global _last_reinvestment_date, _last_daily_summary_date, _consecutive_errors
    _scheduler_state["running"] = True
    _scheduler_debug["step"] = "warmup"
    _scheduler_debug["ts"] = datetime.now().isoformat()
    print("[scheduler] Iniciado - intervalo:", _scheduler_state["interval_minutes"], "min", flush=True)
    # Aguarda o servidor subir completamente E o DB estar pronto antes do primeiro ciclo
    await asyncio.sleep(60)
    _scheduler_debug["step"] = "warmup_done"
    _scheduler_debug["ts"] = datetime.now().isoformat()
    _consecutive_errors = 0
    while _scheduler_state["running"]:
      try:
        _scheduler_debug["loop_count"] += 1
        _scheduler_debug["step"] = "loop_tick"
        _scheduler_debug["ts"] = datetime.now().isoformat()
        print(f"[scheduler] Loop tick - {datetime.now().isoformat()}", flush=True)
        from datetime import timezone, timedelta
        brt = timezone(timedelta(hours=-3))
        now_brt = datetime.now(brt)
        today_str = now_brt.strftime("%Y-%m-%d")

        # ✨ Resumo diário via WhatsApp: na virada do dia (primeiro ciclo do novo dia)
        if _last_daily_summary_date and _last_daily_summary_date != today_str:
            try:
                prev_day = _last_daily_summary_date
                prev_cycles = [c for c in _perf_state.get("cycles", []) if c.get("timestamp", "").startswith(prev_day)]
                prev_pnl = round(sum(c.get("pnl", 0) for c in prev_cycles), 2)
                prev_wins = sum(1 for c in prev_cycles if c.get("pnl", 0) > 0)
                prev_capital = _trade_state.get("capital", 0)
                if ALERTS_AVAILABLE and alert_manager:
                    asyncio.create_task(alert_manager.alert_daily_summary(
                        today_pnl=prev_pnl,
                        capital=prev_capital,
                        total_cycles=len(prev_cycles),
                        win_cycles=prev_wins,
                        date_str=prev_day
                    ))
                    print(f"[scheduler] 📊 Resumo diário enviado para {prev_day}", flush=True)
            except Exception as _e_sum:
                print(f"[scheduler] Erro no resumo diário: {_e_sum}", flush=True)
        _last_daily_summary_date = today_str

        # ✨ Reinvestimento automático: após 17h BRT, reinveste lucro do dia
        if now_brt.weekday() < 5 and now_brt.hour == 17 and _last_reinvestment_date != today_str:
            try:
                today_cycles = [c for c in _perf_state.get("cycles", []) if c.get("timestamp", "").startswith(today_str)]
                today_pnl = round(sum(c.get("pnl", 0) for c in today_cycles), 2)
                if today_pnl != 0:
                    reinvest = round(today_pnl * settings.COMPOUNDING_RATE, 2)
                    _trade_state["capital"] = round(_trade_state["capital"] + reinvest, 2)
                    sinal = "⬆ Lucro" if today_pnl > 0 else "⬇ Prejuízo"
                    _trade_log("REINVESTIMENTO", "—", reinvest,
                        f"💰 {sinal} do dia R$ {today_pnl:+.2f} → reinvestido {settings.COMPOUNDING_RATE*100:.0f}%: R$ {reinvest:+.2f}. Capital: R$ {_trade_state['capital']:.2f}")
                    print(f"[scheduler] Reinvestimento: R$ {today_pnl:+.2f} -> capital agora R$ {_trade_state['capital']:.2f}", flush=True)
                _last_reinvestment_date = today_str
            except Exception as e:
                print(f"[scheduler] Erro no reinvestimento: {e}", flush=True)

        interval_sec = _scheduler_state["interval_minutes"] * 60
        _scheduler_state["next_run"] = datetime.now().isoformat()

        # Determina sessão: B3+Crypto ou Crypto-only
        active_assets, session_label = _current_session()
        _scheduler_state["session"] = session_label

        # Intervalo dinâmico: mais rápido para crypto, mais lento para B3
        if _is_market_open():
            interval_sec = settings.B3_CYCLE_MINUTES * 60
        else:
            interval_sec = settings.CRYPTO_CYCLE_MINUTES * 60  # 10min crypto

        # ── Scalping Turbo: se mercado muito volátil, ciclo de 2min ──────
        # (verificado após o ciclo executar, aplica ao próximo intervalo)

        # ── Proteção: se hard stopped, só verifica a cada 5min sem operar ──
        if _protection_state.get("hard_stopped", False):
            _scheduler_debug["step"] = "hard_stopped"
            _scheduler_debug["ts"] = datetime.now().isoformat()
            print(f"[scheduler] 🔴 HARD STOP ativo — drawdown máximo atingido. Aguardando /trade/unfreeze", flush=True)
            await asyncio.sleep(300)  # verifica a cada 5min
            continue

        _scheduler_debug["step"] = "before_cycle"
        _scheduler_debug["ts"] = datetime.now().isoformat()
        try:
            result = await asyncio.wait_for(
                _run_trade_cycle_internal(assets=active_assets),
                timeout=180  # max 3 min por ciclo
            )
            _scheduler_state["total_auto_cycles"] += 1
            _consecutive_errors = 0  # reset on success
            _scheduler_debug["step"] = "cycle_done"
            _scheduler_debug["ts"] = datetime.now().isoformat()
            _persist_scheduler_state()
            pnl = result.get("cycle_pnl", 0)
            irq = result.get("irq", 0)
            turbo = result.get("turbo_active", False)
            grid_p = result.get("grid_pnl", 0)
            prot = result.get("protection", {})
            prot_info = ""
            if prot.get("paused"):
                prot_info = " | ⏸️ PAUSADO"
            elif prot.get("size_multiplier", 1.0) < 1.0:
                prot_info = f" | ⚠️ {prot['size_multiplier']*100:.0f}% tamanho"
            if prot.get("consecutive_losses", 0) > 0:
                prot_info += f" | 🔻 {prot['consecutive_losses']}x perdas"
            turbo_info = " | 🚀 TURBO" if turbo else ""
            grid_info = f" | Grid: R${grid_p:+.2f}" if grid_p != 0 else ""
            print(
                f"[scheduler] Ciclo #{_scheduler_state['total_auto_cycles']} [{session_label}] "
                f"| P&L: R$ {pnl:.4f} (5m:{result.get('pnl_5m',0):.2f} 1h:{result.get('pnl_1h',0):.2f} 1d:{result.get('pnl_1d',0):.2f})"
                f" | IRQ: {irq:.3f}{grid_info}{turbo_info}{prot_info}",
                flush=True,
            )
            # ── Scalping Turbo: se detectado, próximo ciclo será em 2min ──
            if turbo and settings.TURBO_ENABLED:
                interval_sec = settings.TURBO_CYCLE_SECONDS
                print(f"[scheduler] 🚀 Turbo Mode! Próximo ciclo em {settings.TURBO_CYCLE_SECONDS}s", flush=True)
        except asyncio.TimeoutError:
            err_msg = "Ciclo excedeu timeout de 180s"
            _scheduler_debug["step"] = "timeout"
            _scheduler_debug["ts"] = datetime.now().isoformat()
            print(f"[scheduler] ⏱️ TIMEOUT: {err_msg}", flush=True)
            _last_scheduler_errors.append({"time": datetime.now().isoformat(), "error": err_msg, "traceback": "", "count": 0})
            if len(_last_scheduler_errors) > 10:
                _last_scheduler_errors.pop(0)
        except Exception as e:
            err_msg = str(e)
            print(f"[scheduler] Erro no ciclo automático: {err_msg}", flush=True)
            if ALERTS_AVAILABLE and alert_manager:
                asyncio.create_task(alert_manager.alert_critical_error(
                    error_msg=err_msg,
                    error_type="CICLO_FALHOU"
                ))

        # Alinha o sleep ao fechamento do próximo candle 5m >= interval_sec
        # Garante que o próximo ciclo sempre opera sobre candle completo
        if interval_sec >= 60:  # só alinha se não for turbo (turbo=2min, menor que 1 candle)
            aligned_sec = _align_to_candle_close(interval_sec, candle_minutes=5)
            if aligned_sec != interval_sec:
                print(f"[scheduler] ⏱ Alinhando candle: dormindo {aligned_sec}s (era {interval_sec}s)", flush=True)
            await asyncio.sleep(aligned_sec)
        else:
            await asyncio.sleep(interval_sec)

      except asyncio.CancelledError:
        print("[scheduler] Task cancelada.", flush=True)
        break
      except Exception as _loop_err:
        _consecutive_errors += 1
        import traceback
        _tb = traceback.format_exc()
        _err_entry = {"time": datetime.now().isoformat(), "error": str(_loop_err), "traceback": _tb, "count": _consecutive_errors}
        _last_scheduler_errors.append(_err_entry)
        if len(_last_scheduler_errors) > 10:
            _last_scheduler_errors.pop(0)
        print(f"[scheduler] ❌ Erro GERAL no loop (#{_consecutive_errors}): {_loop_err}\n{_tb}", flush=True)
        _persist_scheduler_state()  # persiste erros consecutivos no DB
        # Back-off: espera 30s * n erros consecutivos (max 5min)
        backoff = min(30 * _consecutive_errors, 300)
        print(f"[scheduler] Aguardando {backoff}s antes de tentar novamente...", flush=True)
        await asyncio.sleep(backoff)
        if _consecutive_errors >= 20:
            print("[scheduler] ❌ 20 erros consecutivos — parando scheduler.", flush=True)
            break
        continue

    print("[scheduler] Parado.", flush=True)


# ═══════════════════════════════════════════
# KEEP-ALIVE: pinga o próprio Railway a cada ~10 min para manter conexões ativas
# ===================================================

async def _keep_alive_loop():
    """Self-ping periódico para manter o serviço Railway ativo."""
    import httpx
    RAILWAY_URL = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    if not RAILWAY_URL:
        return  # não está no Railway, não faz nada
    url = f"https://{RAILWAY_URL}/health"
    while True:
        await asyncio.sleep(600)  # 10 minutos
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.get(url)
        except Exception:
            pass


async def _reconcile_broker_positions():
    """
    Reconcilia posições abertas nos brokers com o _trade_state após um restart.
    Garante que o bot não abra posições duplicadas e não perca rastro de posições existentes.
    """
    if not market_data_service:
        print("[reconcile] market_data_service não disponível, pulando reconciliação.", flush=True)
        return
    try:
        broker_positions = await market_data_service.get_all_positions()
        if not broker_positions:
            print("[reconcile] Nenhuma posição aberta nos brokers.", flush=True)
            return

        state_positions = _trade_state.get("positions", {})
        added, confirmed = 0, 0

        for bp in broker_positions:
            asset    = bp.get("asset", "").upper()
            quantity = float(bp.get("quantity", 0))
            if not asset or quantity <= 0:
                continue

            if asset in state_positions:
                # Posição já conhecida — confirma que ainda está aberta
                state_positions[asset]["quantity_broker"] = quantity
                confirmed += 1
            else:
                # Posição orfã no broker (aberta antes do reset/restart)
                state_positions[asset] = {
                    "amount":          0,           # valor BRL desconhecido neste ponto
                    "action":          "BUY",
                    "tf":              "reconciled",
                    "pct":             0,
                    "classification": "UNKNOWN",
                    "change_pct":      0,
                    "quantity_broker": quantity,
                    "broker":          bp.get("broker", "unknown"),
                    "reconciled":      True,        # flag: veio do broker, não de um ciclo
                }
                added += 1
                print(f"[reconcile] Posição orfã importada do broker: {asset} ({quantity:.6f})", flush=True)

        _trade_state["positions"] = state_positions
        db_state.save_state("trade_state", _trade_state)
        print(
            f"[reconcile] Concluído: {confirmed} confirmadas, {added} importadas do broker, "
            f"{len(state_positions)} total.",
            flush=True,
        )
    except Exception as e:
        print(f"[reconcile] Erro na reconciliação: {e}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicia o scheduler automático quando o servidor sobe."""
    # ══ PASSO 0: Aguardar PostgreSQL estar pronto ANTES de carregar qualquer estado ══
    print("[lifespan] Aguardando PostgreSQL ficar pronto...", flush=True)
    pg_ok = await asyncio.to_thread(db_state.wait_pg_ready, 120, 3.0)
    if not pg_ok:
        print("[lifespan] ⚠️ PostgreSQL não respondeu! Usando dados em memória/JSON local.", flush=True)
    
    # ── Recarregar estado do DB com retry (garante persistência entre deploys) ──
    global _perf_state, _trade_state, _scheduler_state, _consecutive_errors

    async def _load_with_retry(key: str, default: dict, label: str, retries: int = 10, delay: float = 5.0) -> dict:
        """Tenta carregar estado do DB até `retries` vezes com `delay` segundos entre tentativas."""
        for attempt in range(1, retries + 1):
            try:
                data = db_state.load_state(key, {})
                if data:
                    print(f"[lifespan] '{key}' carregado do DB (tentativa {attempt})", flush=True)
                    return data
                if attempt < retries:
                    print(f"[lifespan] '{key}' vazio — aguardando DB... (tentativa {attempt}/{retries})", flush=True)
                    await asyncio.sleep(delay)
            except Exception as e:
                print(f"[lifespan] '{key}' erro na tentativa {attempt}: {e}", flush=True)
                if attempt < retries:
                    await asyncio.sleep(delay)
        print(f"[lifespan] AVISO: '{key}' não carregado após {retries} tentativas — usando estado em memória.", flush=True)
        return {}

    try:
        saved_perf = await _load_with_retry("performance", {}, "performance")
        if saved_perf and "cycles" in saved_perf:
            # Preserva ciclos existentes em memória se o DB retornou menos (evita regressão)
            mem_cycles = len(_perf_state.get("cycles", []))
            db_cycles  = len(saved_perf.get("cycles", []))
            if db_cycles >= mem_cycles:
                _perf_state.update(saved_perf)
            else:
                # Mantém ciclos em memória mas atualiza os demais campos do DB
                saved_perf_no_cycles = {k: v for k, v in saved_perf.items() if k != "cycles"}
                _perf_state.update(saved_perf_no_cycles)
            print(f"[lifespan] Perf state recarregado: {len(_perf_state.get('cycles', []))} ciclos, "
                  f"win={_perf_state.get('win_count',0)}, loss={_perf_state.get('loss_count',0)}", flush=True)
        elif saved_perf:
            _perf_state.update(saved_perf)

        saved_trade = await _load_with_retry("trade_state", {}, "trade_state")
        if saved_trade and saved_trade.get("capital"):
            # Só atualiza se o capital do DB for MAIOR que o da memória (nunca regredir)
            db_capital = saved_trade.get("capital", 0)
            mem_capital = _trade_state.get("capital", 0)
            if db_capital >= mem_capital:
                _trade_state.update(saved_trade)
            else:
                # Mantém capital da memória mas pega o resto (log, posições etc)
                saved_no_capital = {k: v for k, v in saved_trade.items() if k != "capital"}
                _trade_state.update(saved_no_capital)
            print(f"[lifespan] Trade state recarregado: capital=R${_trade_state.get('capital',0):.2f}"
                  f" (DB={db_capital:.2f}, mem={mem_capital:.2f})", flush=True)
        else:
            print(f"[lifespan] AVISO: trade_state NÃO carregado do DB! Usando memória: capital=R${_trade_state.get('capital',0):.2f}", flush=True)

        saved_scheduler = await _load_with_retry("scheduler_state", {}, "scheduler_state")
        if saved_scheduler:
            for key in _SCHEDULER_PERSIST_KEYS:
                if key in saved_scheduler:
                    _scheduler_state[key] = saved_scheduler.get(key)
            if "consecutive_errors" in saved_scheduler:
                _consecutive_errors = int(saved_scheduler.get("consecutive_errors", 0))
            print(
                f"[lifespan] Scheduler recarregado: ciclos={_scheduler_state.get('total_auto_cycles', 0)} "
                f"intervalo={_scheduler_state.get('interval_minutes', 30)}min"
                + (f" | erros_consecutivos={_consecutive_errors}" if _consecutive_errors > 0 else ""),
                flush=True,
            )

        # Evita regressão de contador quando houver histórico de performance maior
        _scheduler_state["total_auto_cycles"] = _effective_total_cycles()
        _persist_scheduler_state()
    except Exception as _e:
        print(f"[lifespan] Aviso ao recarregar estado: {_e}", flush=True)
    # ── Inicializar alertas Telegram/Discord (se configurados) ─────────
    if ALERTS_AVAILABLE and alert_manager:
        if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
            alert_manager.add_telegram("telegram_main", settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
            print("[alerts] Telegram configurado", flush=True)
        if settings.DISCORD_WEBHOOK_URL:
            alert_manager.add_discord("discord_main", settings.DISCORD_WEBHOOK_URL)
            print("[alerts] Discord configurado", flush=True)
        if settings.WHATSAPP_PHONE and settings.WHATSAPP_APIKEY:
            alert_manager.add_whatsapp("whatsapp_main", settings.WHATSAPP_PHONE, settings.WHATSAPP_APIKEY)
            print("[alerts] WhatsApp (CallMeBot) configurado", flush=True)
    # ── Auto-trading ativo por padrão ──────────────────────────────────
    _trade_state["auto_trading"] = True
    # ── Reconciliação de posições com brokers ───────────────────────────
    asyncio.get_event_loop().create_task(_reconcile_broker_positions())
    # ── Scheduler de ciclos ────────────────────────────────────────────
    task = asyncio.create_task(_auto_cycle_loop())
    _scheduler_state["task"] = task
    # ── Keep-alive desativado (bot local) ─────────────────────────────
    keep_alive_task = asyncio.create_task(_keep_alive_loop())
    print("[lifespan] Bot 24/7 ativo — scheduler iniciado", flush=True)
    yield
    # Shutdown
    _scheduler_state["running"] = False
    try:
        db_state.save_state("trade_state", _trade_state)
        db_state.save_state("performance", _perf_state)
        _persist_scheduler_state()
    except Exception:
        pass
    _persist_scheduler_state()
    task.cancel()
    keep_alive_task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    try:
        await keep_alive_task
    except asyncio.CancelledError:
        pass


# Criar aplicação
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Bot de Day Trade Automatizado com Análise de Momentum e Risco",
    lifespan=lifespan,
    dependencies=[Depends(verify_api_key)],
)

# ── CORS — restrito ao domínio do Railway + localhost dev ──────────
_ALLOWED_ORIGINS = [
    "https://daytrade-bot-production.up.railway.app",
    "https://*.up.railway.app",
    "http://localhost:8000",
    "http://localhost:8001",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8001",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)

# ── Security Headers ──────────────────────────────────────────────
app.add_middleware(SecurityHeadersMiddleware)

# ── Rate Limiting ─────────────────────────────────────────────────
if RATE_LIMIT_AVAILABLE and _limiter:
    app.state.limiter = _limiter
    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit excedido. Tente novamente em breve."},
        )

# Static files — serve o dashboard web
_dashboard_dir = Path(__file__).parent.parent / "dashboard-web"
if _dashboard_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(_dashboard_dir), html=True), name="dashboard")

@app.get("/dashboard", include_in_schema=False)
async def redirect_to_dashboard():
    """Redireciona para o dashboard web"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/ui/")


@app.get("/simulador", include_in_schema=False)
async def serve_simulador():
    """Página standalone do Simulador & Testes"""
    sim_file = _dashboard_dir / "simulador.html"
    if sim_file.exists():
        return FileResponse(str(sim_file))
    raise HTTPException(status_code=404, detail="simulador.html não encontrado")


# Dados de teste em memória (será substituído por dados reais via API)
# Precisam de 20+ pontos para period_long=20 funcionar corretamente
test_assets_data = {
    "BTC": {
        "prices": [
            41000, 41500, 41800, 42000, 41700, 42200, 42500, 42300, 42800, 43000,
            42900, 43100, 43200, 43000, 42800, 42900, 43300, 43400, 43500, 43600,
            43700, 43900,
        ],
        "volumes": [
            80, 90, 95, 100, 88, 110, 120, 108, 115, 100,
            92, 105, 120, 110, 90, 95, 130, 140, 125, 115,
            120, 135,
        ],
    },
    "ETH": {
        "prices": [
            2150, 2180, 2200, 2220, 2190, 2210, 2240, 2225, 2260, 2290,
            2280, 2300, 2320, 2310, 2300, 2290, 2310, 2330, 2340, 2350,
            2360, 2375,
        ],
        "volumes": [
            420, 440, 460, 480, 450, 490, 510, 495, 505, 500,
            488, 505, 520, 510, 480, 490, 530, 550, 540, 530,
            540, 560,
        ],
    },
    "BNB": {
        "prices": [
            570, 575, 580, 585, 578, 582, 590, 587, 595, 600,
            598, 602, 605, 602, 598, 597, 605, 610, 612, 615,
            618, 622,
        ],
        "volumes": [
            170, 175, 180, 190, 178, 185, 195, 188, 198, 200,
            192, 200, 210, 205, 190, 195, 220, 230, 225, 220,
            225, 235,
        ],
    },
    "SOL": {
        "prices": [
            160, 163, 166, 169, 165, 168, 172, 170, 175, 178,
            177, 179, 182, 181, 179, 178, 182, 185, 186, 188,
            190, 193,
        ],
        "volumes": [
            250, 260, 265, 275, 262, 270, 285, 278, 290, 300,
            292, 300, 310, 305, 280, 290, 320, 340, 330, 320,
            330, 345,
        ],
    },
    "ADA": {
        "prices": [
            0.95, 0.97, 0.99, 1.01, 0.98, 1.00, 1.03, 1.02, 1.05, 1.07,
            1.06, 1.08, 1.10, 1.09, 1.08, 1.09, 1.11, 1.12, 1.13, 1.14,
            1.15, 1.17,
        ],
        "volumes": [
            800, 850, 880, 920, 860, 900, 950, 930, 960, 1000,
            970, 1000, 1050, 1020, 950, 980, 1100, 1150, 1125, 1100,
            1150, 1200,
        ],
    },
    # B3 fallback data
    "PETR4": {
        "prices": [36.5,36.8,37.0,37.2,36.9,37.1,37.4,37.2,37.6,37.8,
                   37.7,37.9,38.0,37.8,37.6,37.7,38.1,38.3,38.4,38.5,38.6,38.8],
        "volumes": [50,55,58,62,54,60,68,65,70,72,68,72,76,74,65,68,82,88,84,80,84,90],
    },
    "VALE3": {
        "prices": [85.0,85.5,86.0,86.5,85.8,86.2,86.8,86.5,87.2,87.8,
                   87.6,88.0,88.3,88.1,87.9,88.0,88.5,88.9,89.0,89.2,89.4,89.7],
        "volumes": [45,48,52,56,50,54,60,58,63,65,62,65,70,68,60,62,74,80,76,73,76,82],
    },
    "ITUB4": {
        "prices": [32.0,32.3,32.5,32.8,32.4,32.6,32.9,32.7,33.1,33.4,
                   33.3,33.5,33.7,33.6,33.4,33.5,33.8,34.0,34.1,34.2,34.3,34.5],
        "volumes": [40,43,46,50,44,48,54,51,56,58,55,58,63,60,53,55,66,71,68,65,68,73],
    },
    "BBDC4": {
        "prices": [18.0,18.2,18.3,18.5,18.2,18.3,18.5,18.4,18.7,18.9,
                   18.8,19.0,19.1,19.0,18.8,18.9,19.1,19.3,19.4,19.5,19.6,19.7],
        "volumes": [30,32,35,38,33,36,40,38,42,44,41,44,47,45,40,41,49,53,51,49,51,55],
    },
    "ABEV3": {
        "prices": [14.0,14.1,14.2,14.3,14.1,14.2,14.4,14.3,14.5,14.7,
                   14.6,14.8,14.9,14.8,14.7,14.8,14.9,15.0,15.1,15.1,15.2,15.3],
        "volumes": [25,27,29,31,28,30,33,31,34,36,34,36,38,37,33,34,40,43,41,40,41,44],
    },
    "WEGE3": {
        "prices": [42.0,42.3,42.6,42.9,42.5,42.7,43.1,42.9,43.4,43.7,
                   43.6,43.9,44.1,43.9,43.7,43.8,44.2,44.5,44.6,44.7,44.9,45.1],
        "volumes": [20,22,24,26,23,25,28,26,29,31,29,31,33,32,28,29,35,38,36,35,36,39],
    },
    "MGLU3": {
        "prices": [4.50,4.55,4.60,4.65,4.58,4.62,4.68,4.65,4.72,4.78,
                   4.76,4.80,4.83,4.81,4.79,4.80,4.85,4.89,4.90,4.92,4.94,4.97],
        "volumes": [120,128,137,146,131,141,154,147,159,165,158,165,174,170,151,156,187,201,192,185,192,207],
    },
    "BBAS3": {
        "prices": [52.0,52.4,52.8,53.2,52.7,53.0,53.5,53.2,53.8,54.2,
                   54.0,54.4,54.7,54.5,54.2,54.3,54.8,55.1,55.3,55.4,55.6,55.9],
        "volumes": [35,37,40,43,38,41,45,43,47,50,47,50,53,52,46,47,57,62,59,57,59,63],
    },
    "ITSA4": {
        "prices": [10.0,10.1,10.2,10.3,10.2,10.2,10.3,10.3,10.4,10.5,
                   10.5,10.6,10.6,10.6,10.5,10.5,10.6,10.7,10.8,10.8,10.8,10.9],
        "volumes": [55,58,62,66,60,64,70,67,73,76,72,76,80,78,70,71,86,92,88,85,88,94],
    },
    "RENT3": {
        "prices": [62.0,62.5,63.0,63.5,63.0,63.3,63.8,63.5,64.2,64.7,
                   64.5,64.9,65.2,65.0,64.8,64.9,65.4,65.8,65.9,66.1,66.3,66.6],
        "volumes": [18,19,21,22,20,21,23,22,24,26,24,26,28,27,24,24,29,32,31,29,31,33],
    },
}


@app.get("/", include_in_schema=False)
async def root():
    """Redireciona para o dashboard web"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/ui/")


@app.get("/api", tags=["Status"])
async def api_status():
    """Status da API"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "message": "Bot de Day Trade Automatizado",
        "dashboard": "/ui/",
    }




@app.get("/backup-state")
async def backup_state():
    """Retorna snapshot completo do estado atual (trade + performance).
    Chamar SEMPRE antes de qualquer modificação manual de dados.
    """
    import copy
    return {
        "timestamp": _brt_now().isoformat(),
        "trade_state": copy.deepcopy(_trade_state),
        "perf_state": {
            "total_pnl_offset": _perf_state.get("total_pnl_offset", 0.0),
            "total_gain": _perf_state.get("total_gain", 0.0),
            "total_loss": _perf_state.get("total_loss", 0.0),
            "win_count": _perf_state.get("win_count", 0),
            "loss_count": _perf_state.get("loss_count", 0),
            "cycles_count": len(_perf_state.get("cycles", [])),
            "cycles": _perf_state.get("cycles", []),
        },
    }

@app.get("/health")
async def health_check():
    """Health check — usado pelo keep-alive e monitoramento externo."""
    from datetime import timezone as _tz, timedelta as _td
    _brt = _tz(_td(hours=-3))
    return {
        "status": "ok",
        "deploy_version": "v2026.03.09-health-monitor",
        "timestamp": datetime.now(_brt).isoformat(),
        "auto_trading": _trade_state.get("auto_trading", False),
        "scheduler_running": _scheduler_state.get("running", False),
        "total_cycles": _effective_total_cycles(),
        "last_cycle": _trade_state.get("last_cycle"),
        "uptime_session": _scheduler_state.get("session", ""),
        "persistence": db_state.storage_info(),
    }


# ═══════════════════════════════════════════
# DIAGNÓSTICO / HEALTH MONITOR
# ═══════════════════════════════════════════

@app.get("/diagnostics", tags=["Monitoramento"])
async def diagnostics():
    """Auto-diagnóstico completo do bot — usado pelo banner de saúde no dashboard."""
    from datetime import timezone as _tz, timedelta as _td
    _brt = _tz(_td(hours=-3))
    now = datetime.now(_brt)

    issues: list[dict] = []
    severity = "healthy"  # healthy | warning | critical

    # ── 1. Scheduler rodando? ────────────────────────────────
    sched_running = _scheduler_state.get("running", False)
    sched_task = _scheduler_state.get("task")
    task_alive = sched_task is not None and not sched_task.done() if sched_task else False

    if not sched_running and not task_alive:
        issues.append({
            "id": "scheduler_stopped",
            "level": "critical",
            "title": "Scheduler Parado",
            "detail": "O agendador automático não está rodando. Nenhum ciclo será executado.",
            "action": "Reiniciar scheduler via /scheduler/start",
        })
        severity = "critical"
    elif sched_running and not task_alive:
        issues.append({
            "id": "scheduler_zombie",
            "level": "critical",
            "title": "Scheduler Zumbi",
            "detail": "O scheduler diz que está rodando mas a task interna morreu.",
            "action": "Reiniciar scheduler",
        })
        severity = "critical"

    # ── 2. Último ciclo está travado? ────────────────────────
    last_cycle_str = _trade_state.get("last_cycle")
    cycle_stale_minutes = None
    if last_cycle_str:
        try:
            last_dt = datetime.fromisoformat(str(last_cycle_str))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=_brt)
            delta = now - last_dt
            cycle_stale_minutes = delta.total_seconds() / 60
            # Só alerta se mercado aberto (B3 ou crypto) E ciclo parado > 45 min
            is_market = _is_market_open()
            if cycle_stale_minutes > 45 and (is_market or True):  # crypto 24/7
                level = "critical" if cycle_stale_minutes > 90 else "warning"
                issues.append({
                    "id": "cycle_stale",
                    "level": level,
                    "title": f"Ciclo Parado há {int(cycle_stale_minutes)} min",
                    "detail": f"Último ciclo executado em {last_dt.strftime('%H:%M BRT')}. "
                              f"Já se passaram {int(cycle_stale_minutes)} minutos sem novo ciclo.",
                    "action": "Verificar logs do scheduler ou forçar /trade/cycle",
                })
                if level == "critical":
                    severity = "critical"
                elif severity != "critical":
                    severity = "warning"
        except Exception:
            pass

    # ── 3. Erros recentes no scheduler ───────────────────────
    recent_errors = _last_scheduler_errors[-5:]
    if recent_errors:
        last_err = recent_errors[-1]
        err_time = last_err.get("time", "?")
        err_msg = last_err.get("error", "desconhecido")
        level = "critical" if len(recent_errors) >= 3 else "warning"
        issues.append({
            "id": "scheduler_errors",
            "level": level,
            "title": f"{len(recent_errors)} Erro(s) Recentes",
            "detail": f"Último erro: {err_msg} ({err_time})",
            "action": "Ver /scheduler/status para detalhes completos",
        })
        if level == "critical" and severity != "critical":
            severity = "critical"
        elif severity == "healthy":
            severity = "warning"

    # ── 4. Hard stop / proteção ativa ────────────────────────
    if _protection_state.get("hard_stopped", False):
        issues.append({
            "id": "hard_stopped",
            "level": "critical",
            "title": "HARD STOP Ativo",
            "detail": "Drawdown máximo atingido. Bot não está operando.",
            "action": "Usar /trade/unfreeze para desbloquear",
        })
        severity = "critical"
    elif _protection_state.get("paused", False):
        issues.append({
            "id": "protection_paused",
            "level": "warning",
            "title": "Proteção: Bot Pausado",
            "detail": f"Perdas consecutivas: {_protection_state.get('consecutive_losses', 0)}",
            "action": "Aguardar auto-despause ou /trade/unfreeze",
        })
        if severity == "healthy":
            severity = "warning"

    # ── 5. Consecutive errors alto ───────────────────────────
    if _consecutive_errors >= 5:
        issues.append({
            "id": "consecutive_errors",
            "level": "critical",
            "title": f"{_consecutive_errors} Erros Consecutivos",
            "detail": "O scheduler pode parar automaticamente em 20 erros consecutivos.",
            "action": "Verificar logs e reiniciar scheduler",
        })
        severity = "critical"
    elif _consecutive_errors >= 2:
        issues.append({
            "id": "consecutive_errors",
            "level": "warning",
            "title": f"{_consecutive_errors} Erros Consecutivos",
            "detail": "O scheduler está em backoff mas continua tentando.",
            "action": "Monitorar ou reiniciar scheduler",
        })
        if severity == "healthy":
            severity = "warning"

    # ── 6. Debug step anormal ────────────────────────────────
    debug_step = _scheduler_debug.get("step", "init")
    debug_ts = _scheduler_debug.get("ts", "")
    if debug_step in ("timeout", "hard_stopped"):
        if debug_step == "timeout":
            issues.append({
                "id": "last_timeout",
                "level": "warning",
                "title": "Último Ciclo Deu Timeout",
                "detail": f"O ciclo excedeu 180s em {debug_ts}",
                "action": "Verificar se API externa está lenta",
            })
            if severity == "healthy":
                severity = "warning"

    # ── 7. Auto-trading desligado ────────────────────────────
    if not _trade_state.get("auto_trading", True):
        issues.append({
            "id": "auto_trading_off",
            "level": "warning",
            "title": "Auto-Trading Desligado",
            "detail": "O bot está analisando mas não executa operações.",
            "action": "Ligar auto-trading no painel Trade",
        })
        if severity == "healthy":
            severity = "warning"

    return {
        "success": True,
        "severity": severity,       # "healthy" | "warning" | "critical"
        "issues_count": len(issues),
        "issues": issues,
        "meta": {
            "checked_at": now.isoformat(),
            "scheduler_running": sched_running,
            "task_alive": task_alive,
            "last_cycle": last_cycle_str,
            "cycle_stale_minutes": round(cycle_stale_minutes, 1) if cycle_stale_minutes else None,
            "consecutive_errors": _consecutive_errors,
            "debug_step": debug_step,
            "loop_count": _scheduler_debug.get("loop_count", 0),
            "total_cycles": _effective_total_cycles(),
        },
    }


@app.post("/analyze/momentum")
async def analyze_momentum():
    """Analisa momentum de todos os ativos"""
    try:
        results = MomentumAnalyzer.calculate_multiple_assets(test_assets_data)

        return {
            "success": True,
            "message": "Análise de momentum concluída",
            "data": {asset: data for asset, data in results.items()},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze/risk")
async def analyze_risk():
    """Analisa risco global do mercado (IRQ)"""
    try:
        # Usar dados de BTC como referência para risco geral
        btc_data = test_assets_data["BTC"]
        risk_analysis = RiskAnalyzer.calculate_irq(
            btc_data["prices"],
            btc_data["volumes"],
        )

        protection = RiskAnalyzer.get_protection_level(risk_analysis["irq_score"])
        risk_analysis["protection"] = protection

        return {
            "success": True,
            "message": "Análise de risco concluída",
            "data": risk_analysis,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze/full")
async def full_analysis():
    """Análise completa: Momentum + Risco + Alocação (dados REAIS quando disponíveis)"""
    try:
        # Tentar dados reais primeiro, fallback para test
        live_data = None
        if MARKET_DATA_AVAILABLE and market_data_service:
            try:
                live_data = await market_data_service.get_all_klines(
                    settings.ALL_ASSETS, "5m", 25
                )
            except Exception:
                pass
        source_data = live_data if live_data and len(live_data) > 0 else test_assets_data

        # 1. Analisar Momentum
        momentum_results = MomentumAnalyzer.calculate_multiple_assets(source_data)
        momentum_scores = {asset: data["momentum_score"] for asset, data in momentum_results.items()}

        # 2. Analisar Risco Global (BTC como ref, fallback para primeiro ativo)
        ref_asset = "BTC" if "BTC" in source_data else list(source_data.keys())[0]
        ref_data = source_data[ref_asset]
        risk_analysis = RiskAnalyzer.calculate_irq(
            ref_data.get("prices", ref_data) if isinstance(ref_data, dict) else ref_data,
            ref_data.get("volumes", []) if isinstance(ref_data, dict) else [],
        )
        irq_score = risk_analysis["irq_score"]
        protection = RiskAnalyzer.get_protection_level(irq_score)

        # 3. Calcular Alocação — usa capital real do trade se disponível
        initial_capital = _trade_state.get("capital", settings.INITIAL_CAPITAL)
        allocation = PortfolioManager.calculate_portfolio_allocation(
            momentum_scores,
            irq_score,
            initial_capital,
        )

        # 4. Aplicar Rebalanceamento
        rebalancing = PortfolioManager.apply_rebalancing_rules(
            allocation,
            momentum_results,
            initial_capital,
            irq_score,
        )

        # 5. Calcular Métricas de Risco
        risk_metrics = PortfolioManager.calculate_risk_metrics(allocation, initial_capital)

        # Preparar resposta
        analysis_report = {
            "timestamp": datetime.utcnow(),
            "momentum_analysis": {
                asset: {
                    "momentum_score": momentum_results[asset]["momentum_score"],
                    "trend_status": momentum_results[asset]["trend_status"],
                    "classification": momentum_results[asset]["classification"],
                    "return_pct": momentum_results[asset]["return_pct"],
                }
                for asset in momentum_results
            },
            "risk_analysis": {
                "irq_score": irq_score,
                "level": protection["level"],
                "protection_level": protection["level"],  # compatibilidade com dashboard
                "color": protection["color"],
                "reduction_percentage": protection["reduction_percentage"],
                "s1_trend_loss": risk_analysis["s1_trend_loss"],
                "s2_selling_pressure": risk_analysis["s2_selling_pressure"],
                "s3_volatility": risk_analysis["s3_volatility"],
                "s4_rsi_divergence": risk_analysis["s4_rsi_divergence"],
                "s5_losing_streak": risk_analysis["s5_losing_streak"],
                "rsi": risk_analysis["rsi"],
                # Formato para dashboard.js (radar chart)
                "signal_scores": {
                    "S1": risk_analysis["s1_trend_loss"],
                    "S2": risk_analysis["s2_selling_pressure"],
                    "S3": risk_analysis["s3_volatility"],
                    "S4": risk_analysis["s4_rsi_divergence"],
                    "S5": risk_analysis["s5_losing_streak"],
                },
            },
            "allocations": rebalancing,
            # Formato para dashboard.js (portfolio cards)
            "portfolio_allocation": {
                "allocation": {
                    asset: data["recommended_amount"]
                    for asset, data in rebalancing.items()
                }
            },
            "risk_metrics": risk_metrics,
            "capital_info": {
                "total_capital": initial_capital,
                "total_allocated": risk_metrics["total_allocated"],
                "cash_available": risk_metrics["cash_available"],
                "active_positions": risk_metrics["active_positions"],
            },
        }

        return {
            "success": True,
            "message": "Análise completa concluída",
            "data": analysis_report,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/status")
async def bot_status():
    """Status do bot"""
    try:
        # Análise rápida
        momentum_results = MomentumAnalyzer.calculate_multiple_assets(test_assets_data)
        btc_data = test_assets_data["BTC"]
        risk_analysis = RiskAnalyzer.calculate_irq(btc_data["prices"], btc_data["volumes"])

        momentum_scores = {asset: data["momentum_score"] for asset, data in momentum_results.items()}
        real_capital = _trade_state.get("capital", settings.INITIAL_CAPITAL)
        allocation = PortfolioManager.calculate_portfolio_allocation(
            momentum_scores,
            risk_analysis["irq_score"],
            real_capital,
        )
        risk_metrics = PortfolioManager.calculate_risk_metrics(allocation, real_capital)

        status = {
            "is_running": True,
            "last_analysis": datetime.utcnow(),
            "total_capital": real_capital,
            "current_balance": real_capital,
            "cash_available": risk_metrics["cash_available"],
            "active_positions": risk_metrics["active_positions"],
            "total_unrealized_pnl": 0.0,
            "irq_score": risk_analysis["irq_score"],
            "protection_level": RiskAnalyzer.get_protection_level(risk_analysis["irq_score"])["level"],
        }

        return {
            "success": True,
            "message": "Status obtido com sucesso",
            "data": status,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/config")
async def get_config():
    """Retorna configurações do bot"""
    return {
        "success": True,
        "message": "Configurações obtidas",
        "data": {
            "initial_capital": settings.INITIAL_CAPITAL,
            "max_position_percentage": settings.MAX_POSITION_PERCENTAGE,
            "min_position_amount": settings.MIN_POSITION_AMOUNT,
            "stop_loss_percentage": settings.STOP_LOSS_PERCENTAGE,
            "rebalance_interval_seconds": settings.REBALANCE_INTERVAL,
            "irq_thresholds": {
                "high": settings.IRQ_THRESHOLD_HIGH,
                "very_high": settings.IRQ_THRESHOLD_VERY_HIGH,
                "critical": settings.IRQ_THRESHOLD_CRITICAL,
            },
            "allowed_assets": settings.ALLOWED_ASSETS,
        },
    }


@app.post("/predict/ml")
async def predict_with_ml():
    """Predição com Machine Learning"""
    if not ML_AVAILABLE:
        raise HTTPException(status_code=503, detail="Módulo ML não disponível. Instale: pip install httpx")
    try:
        ml = MLEnsemble()
        ml.train(test_assets_data)
        
        predictions = ml.predict_all(list(test_assets_data.keys()))
        
        return {
            "success": True,
            "message": "Predições de ML geradas",
            "data": predictions,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/predict/combined")
async def combined_prediction():
    """Predição combinada: ML + Momentum + Risco"""
    if not ML_AVAILABLE:
        raise HTTPException(status_code=503, detail="Módulo ML não disponível. Instale: pip install httpx")
    try:
        # Análise de Momentum
        momentum_results = MomentumAnalyzer.calculate_multiple_assets(test_assets_data)
        
        # Análise de Risco
        btc_data = test_assets_data["BTC"]
        risk_analysis = RiskAnalyzer.calculate_irq(btc_data["prices"], btc_data["volumes"])
        irq_score = risk_analysis["irq_score"]
        
        # Predições ML
        ml = MLEnsemble()
        ml.train(test_assets_data)
        ml_signals = ml.predict_all(list(test_assets_data.keys()))
        
        # Combinar recomendações
        combined_recommendations = []
        for asset in test_assets_data.keys():
            ml_signal = next((s for s in ml_signals if s["asset"] == asset), None)
            momentum_score = momentum_results[asset]["momentum_score"]
            
            if ml_signal:
                rec = ml.get_recommendation(ml_signal, momentum_score, irq_score)
                combined_recommendations.append(rec)
        
        return {
            "success": True,
            "message": "Predição combinada gerada",
            "data": {
                "recommendations": combined_recommendations,
                "irq_score": irq_score,
                "timestamp": datetime.utcnow(),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/alerts/status")
async def alerts_status():
    """Status do sistema de alertas"""
    if not ALERTS_AVAILABLE or alert_manager is None:
        return {"success": False, "message": "Módulo de alertas não disponível", "data": {}}
    return {
        "success": True,
        "message": "Status de alertas",
        "data": alert_manager.get_status(),
    }


# ── Chat IA com contexto do bot (Gemini) ─────────────────────────────────────
import os as _os

try:
    import google.generativeai as _genai
    _GEMINI_KEY = _os.environ.get("GEMINI_API_KEY", "")
    if _GEMINI_KEY:
        _genai.configure(api_key=_GEMINI_KEY)
        _gemini_model = _genai.GenerativeModel("gemini-2.0-flash-lite")
        _GEMINI_OK = True
    else:
        _gemini_model = None
        _GEMINI_OK = False
except Exception:
    _gemini_model = None
    _GEMINI_OK = False


def _build_bot_context() -> str:
    """Monta contexto real do bot para enviar ao Gemini."""
    cap    = _trade_state.get("capital", 0)
    pnl    = _trade_state.get("total_pnl", 0)
    pos    = _trade_state.get("positions", {})
    cycles = _trade_state.get("cycle_count", 0)
    regime = _trade_state.get("last_regime", "NEUTRAL")

    pos_lines = []
    for asset, p in list(pos.items())[:6]:
        amt = p.get("amount", 0) if isinstance(p, dict) else p
        chg = p.get("change_pct", 0) if isinstance(p, dict) else 0
        tf  = p.get("tf", "?") if isinstance(p, dict) else "?"
        pos_lines.append(f"  - {asset}: R${amt:.2f} | {chg:+.2f}% | tf={tf}")

    # Últimas entradas do log de auditoria
    audit_lines = []
    try:
        import json as _json
        _audit_path = Path(__file__).parent.parent / "data" / "audit_log.json"
        if _audit_path.exists():
            with open(_audit_path, "r", encoding="utf-8") as f:
                _audit = _json.load(f)
            recent = _audit[-8:] if isinstance(_audit, list) else []
            for e in reversed(recent):
                ts   = e.get("timestamp", "")[:16]
                typ  = e.get("type", "")
                note = e.get("note", "")[:120]
                audit_lines.append(f"  [{ts}] {typ}: {note}")
    except Exception:
        pass

    perf = {}
    try:
        import json as _json
        _perf_path = Path(__file__).parent.parent / "data" / "performance.json"
        if _perf_path.exists():
            with open(_perf_path, "r", encoding="utf-8") as f:
                perf = _json.load(f)
    except Exception:
        pass

    wr  = perf.get("win_rate", 0)
    pf  = perf.get("profit_factor", 0)
    sh  = perf.get("sharpe_ratio", 0)
    tot = perf.get("total_trades", 0)

    ctx = f"""=== CONTEXTO DO BOT PWD TRADING ===
Capital atual: R${cap:.2f}
PnL total acumulado: R${pnl:.2f}
Ciclos executados: {cycles}
Regime de mercado: {regime}
Posições abertas ({len(pos)}):
{chr(10).join(pos_lines) if pos_lines else "  Nenhuma"}

Métricas de performance:
  Win Rate: {wr:.1%}
  Profit Factor: {pf:.2f}
  Sharpe Ratio: {sh:.2f}
  Total trades: {tot}

Últimos eventos do log:
{chr(10).join(audit_lines) if audit_lines else "  Sem registros recentes"}
==================================="""
    return ctx


class _ChatRequest(BaseModel):
    message: str


@app.post("/api/chat", tags=["IA"])
async def chat_ia(req: _ChatRequest, request: Request, _auth: str = Depends(verify_api_key)):
    """Chat com IA Gemini com contexto real do bot."""
    import traceback as _tb

    if not _GEMINI_OK or _gemini_model is None:
        raise HTTPException(
            status_code=503,
            detail="Gemini não configurado. Adicione GEMINI_API_KEY no Railway."
        )

    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="Mensagem vazia.")
    if len(user_msg) > 1000:
        raise HTTPException(status_code=400, detail="Mensagem muito longa (máx 1000 chars).")

    try:
        bot_ctx = _build_bot_context()

        system_prompt = (
            "Você é um assistente especializado em trading algorítmico analisando o bot PWD Trading.\n"
            "Seja direto, técnico e objetivo. Responda em português.\n"
            "Use os dados reais do bot abaixo para embasar sua resposta. Não invente dados.\n\n"
            + bot_ctx
        )
        full_prompt = system_prompt + "\n\nUsuário: " + user_msg

        response = await asyncio.wait_for(
            asyncio.to_thread(_gemini_model.generate_content, full_prompt),
            timeout=25.0
        )
        reply = response.text.strip()
        return {"reply": reply}

    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Gemini demorou demais. Tente novamente.")
    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "ResourceExhausted" in err_str or "quota" in err_str.lower():
            raise HTTPException(
                status_code=429,
                detail="Cota da API Gemini atingida. Aguarde 1 minuto e tente novamente (free tier: 30 req/min)."
            )
        _detail = f"{type(e).__name__}: {e}\n{_tb.format_exc()[-800:]}"
        raise HTTPException(status_code=502, detail=_detail)


@app.get("/alerts/history")
async def alerts_history(limit: int = 20):
    """Histórico de alertas"""
    if not ALERTS_AVAILABLE or alert_manager is None:
        return {"success": False, "message": "Módulo de alertas não disponível", "data": []}
    return {
        "success": True,
        "message": "Histórico de alertas",
        "data": alert_manager.get_alert_history(limit),
    }


class _TelegramSetup(BaseModel):
    bot_token: str
    chat_id: str

@app.post("/alerts/setup-telegram")
async def setup_telegram_alerts(body: _TelegramSetup):
    """Configura alertas Telegram (via JSON body — nunca passe tokens na URL)."""
    bot_token = body.bot_token
    chat_id = body.chat_id
    if not ALERTS_AVAILABLE or alert_manager is None:
        raise HTTPException(status_code=503, detail="Módulo de alertas não disponível. Instale: pip install httpx")
    try:
        success = alert_manager.add_telegram("telegram_main", bot_token, chat_id)
        return {
            "success": success,
            "message": "Telegram configurado" if success else "Falha ao configurar Telegram",
            "enabled": success,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class _DiscordSetup(BaseModel):
    webhook_url: str

@app.post("/alerts/setup-discord")
async def setup_discord_alerts(body: _DiscordSetup):
    """Configura alertas Discord (via JSON body — nunca passe tokens na URL)."""
    webhook_url = body.webhook_url
    if not ALERTS_AVAILABLE or alert_manager is None:
        raise HTTPException(status_code=503, detail="Módulo de alertas não disponível. Instale: pip install httpx")
    try:
        success = alert_manager.add_discord("discord_main", webhook_url)
        return {
            "success": success,
            "message": "Discord configurado" if success else "Falha ao configurar Discord",
            "enabled": success,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/alerts/test")
async def test_alert(title: str = "Test Alert", message: str = "This is a test alert from Day Trade Bot"):
    """Envia um alerta de teste"""
    if not ALERTS_AVAILABLE or alert_manager is None:
        raise HTTPException(status_code=503, detail="Módulo de alertas não disponível. Instale: pip install httpx")
    try:
        import asyncio
        asyncio.create_task(
            alert_manager.send_alert(
                "test_alert",
                title,
                message,
                AlertLevel.INFO
            )
        )
        return {
            "success": True,
            "message": "Alerta de teste enviado",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════
# ENDPOINTS: DADOS DE MERCADO (BINANCE)
# ═══════════════════════════════════════════

@app.get("/market/prices")
async def get_market_prices():
    """Obtém preços atuais da brapi.dev (B3)"""
    if not MARKET_DATA_AVAILABLE:
        raise HTTPException(status_code=503, detail="Serviço de dados de mercado não disponível. Instale: pip install httpx")
    try:
        prices = await market_data_service.get_all_prices(settings.ALL_ASSETS)
        return {
            "success": True,
            "message": f"Preços obtidos para {len(prices)} ativos",
            "data": prices,
            "source": "yahoo.finance",
            "timestamp": datetime.utcnow(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Funções de previsão matemática ─────────────────────────────────────────

def _linear_regression_predict(prices: list, steps_ahead: int):
    """
    Regressão linear por mínimos quadrados.
    Retorna (preco_previsto, desvio_padrao_dos_residuos).
    """
    n = len(prices)
    if n < 3:
        return prices[-1], 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(prices) / n
    num = sum((i - x_mean) * (prices[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return prices[-1], 0.0
    b = num / den
    a = y_mean - b * x_mean
    predicted = a + b * (n - 1 + steps_ahead)
    residuals = [prices[i] - (a + b * i) for i in range(n)]
    variance = sum(r ** 2 for r in residuals) / n
    std_dev = variance ** 0.5
    return round(predicted, 4), round(std_dev, 4)


def _ema_project(prices: list, period: int, steps_ahead: int) -> float:
    """
    Calcula EMA e extrapola steps_ahead pontos a frente
    usando a inclinacao media dos ultimos N pontos da serie EMA.
    """
    if not prices:
        return 0.0
    period = min(period, len(prices))
    alpha = 2.0 / (period + 1)
    ema = prices[0]
    ema_series = [ema]
    for p in prices[1:]:
        ema = p * alpha + ema * (1 - alpha)
        ema_series.append(ema)
    look = min(5, len(ema_series) - 1)
    slope = (ema_series[-1] - ema_series[-1 - look]) / look if look > 0 else 0.0
    return round(ema + slope * steps_ahead, 4)


@app.get("/market/predict")
async def get_market_predictions():
    """
    Calcula projecoes matematicas de preco para 1h e 1 dia usando:
      - Regressao linear (minimos quadrados)
      - EMA extrapolada (media exponencial com projecao da inclinacao)
      - Bandas de confianca (+-1 desvio padrao dos residuos)

    Horizonte 1h  -> candles de 5m, 12 passos a frente
    Horizonte 1 dia -> candles de 1d, 1 passo a frente
    """
    if not MARKET_DATA_AVAILABLE:
        raise HTTPException(status_code=503, detail="Servico de dados de mercado nao disponivel")
    try:
        assets = list(settings.ALL_ASSETS)

        # Busca sequencial para evitar contencao de semaforo (80 assets x 2 intervalos)
        klines_5m = await market_data_service.get_all_klines(assets, "5m", 35)
        klines_1d = await market_data_service.get_all_klines(assets, "1d", 35)

        predictions = {}
        for asset in assets:
            prices_5m = klines_5m.get(asset, {}).get("prices", [])
            prices_1d = klines_1d.get(asset, {}).get("prices", [])

            # Previsao 1 hora (12 candles de 5m a frente)
            if len(prices_5m) < 5:
                continue
            lr_1h, std_1h = _linear_regression_predict(prices_5m, 12)
            ema_1h        = _ema_project(prices_5m, 14, 12)
            pred_1h       = round(lr_1h * 0.6 + ema_1h * 0.4, 4)
            current       = prices_5m[-1]
            change_1h_pct = round((pred_1h - current) / current * 100, 3) if current else 0
            conf_low_1h   = round(pred_1h - std_1h, 4)
            conf_high_1h  = round(pred_1h + std_1h, 4)

            # Previsao 1 dia
            if len(prices_1d) >= 5:
                lr_1d, std_1d = _linear_regression_predict(prices_1d, 1)
                ema_1d        = _ema_project(prices_1d, 14, 1)
                pred_1d       = round(lr_1d * 0.6 + ema_1d * 0.4, 4)
                current_1d    = prices_1d[-1]
            else:
                # Fallback: projeta 96 candles de 5m (~8h de pregao)
                lr_1d, std_1d = _linear_regression_predict(prices_5m, 96)
                ema_1d        = _ema_project(prices_5m, 14, 96)
                pred_1d       = round(lr_1d * 0.6 + ema_1d * 0.4, 4)
                current_1d    = prices_5m[-1]

            change_1d_pct = round((pred_1d - current_1d) / current_1d * 100, 3) if current_1d else 0
            conf_low_1d   = round(pred_1d - std_1d, 4)
            conf_high_1d  = round(pred_1d + std_1d, 4)

            # Grau de confianca: coeficiente de variacao dos residuos
            cv = (std_1h / current * 100) if current else 100
            if cv < 0.3:   confidence = "Alta"
            elif cv < 0.8: confidence = "Media"
            else:          confidence = "Baixa"

            predictions[asset] = {
                "current":         round(current, 4),
                "pred_1h":         pred_1h,
                "change_1h_pct":   change_1h_pct,
                "conf_low_1h":     conf_low_1h,
                "conf_high_1h":    conf_high_1h,
                "pred_1d":         pred_1d,
                "change_1d_pct":   change_1d_pct,
                "conf_low_1d":     conf_low_1d,
                "conf_high_1d":    conf_high_1d,
                "confidence":      confidence,
                "candles_used_1h": len(prices_5m),
                "candles_used_1d": len(prices_1d),
            }

        return {
            "success": True,
            "data": predictions,
            "note": "Previsao matematica baseada em regressao linear + EMA. Nao e recomendacao financeira.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Score de Oportunidade + Notícias RSS ───────────────────────────────────
import time as _time_module
import urllib.request as _urllib_req
import xml.etree.ElementTree as _ET

_news_cache: dict = {"data": {}, "ts": 0.0}
_NEWS_TTL = 600  # 10 minutos

_ASSET_KEYWORDS: dict = {
    # B3 — Petróleo & Energia
    "PETR4": ["petrobras", "petr4", "petróleo", "pré-sal", "óleo"],
    "PRIO3": ["petrorio", "prio3", "petróleo", "bacia de campos"],
    "CSAN3": ["cosan", "csan3", "raízen", "comgás"],
    "EGIE3": ["engie", "egie3", "energia elétrica", "geração"],
    # B3 — Mineração & Siderurgia
    "VALE3": ["vale", "vale3", "minério", "minério de ferro", "níquel"],
    "GGBR4": ["gerdau", "ggbr4", "aço", "siderurgia"],
    # B3 — Bancos & Finanças
    "ITUB4": ["itaú", "itub4", "itaú unibanco"],
    "BBDC4": ["bradesco", "bbdc4"],
    "BBAS3": ["banco do brasil", "bbas3"],
    "ITSA4": ["itaúsa", "itsa4"],
    # B3 — Consumo & Varejo
    "ABEV3": ["ambev", "abev3", "cerveja", "bebida"],
    "MGLU3": ["magazine luiza", "magalu", "mglu3", "varejo"],
    "LREN3": ["lojas renner", "lren3", "moda", "varejo de moda"],
    # B3 — Indústria & Tecnologia
    "WEGE3": ["weg", "wege3", "motor", "elétrico"],
    "EMBR3": ["embraer", "embr3", "avião", "aeronave", "aviação"],
    # B3 — Logística & Locação
    "RENT3": ["localiza", "rent3", "aluguel de carros"],
    # B3 — Alimentos
    "JBSS3": ["jbs", "jbss3", "carne", "frigorífico", "exportação carne"],
    # B3 — Papel & Celulose
    "SUZB3": ["suzano", "suzb3", "celulose", "papel", "eucalipto"],
    # B3 — Telecom
    "VIVT3": ["vivo", "vivt3", "telefônica", "telecom", "telecomunicações"],
    # B3 — Saúde
    "RDOR3": ["rede d'or", "rdor3", "hospital", "saúde"],
    # Crypto
    "BTC":  ["bitcoin", "btc", "criptomoeda", "crypto"],
    "ETH":  ["ethereum", "eth", "ether"],
    "BNB":  ["bnb", "binance coin", "binance"],
    "SOL":  ["solana", "sol"],
    "ADA":  ["cardano", "ada"],
    "XRP":  ["xrp", "ripple", "xrp ledger"],
    "DOGE": ["dogecoin", "doge", "meme coin"],
    "AVAX": ["avalanche", "avax"],
    "DOT":  ["polkadot", "dot"],
    "LINK": ["chainlink", "link"],
}

_POS_WORDS = [
    "alta", "sobe", "subiu", "subindo", "valoriza", "valorização",
    "lucro", "crescimento", "compra", "positivo", "forte",
    "recuperação", "expansão", "dividendo", "supera", "recorde",
    "acima do esperado", "resultado positivo",
]

_NEG_WORDS = [
    "queda", "cai", "caiu", "caindo", "desvaloriza", "desvalorização",
    "prejuízo", "baixa", "venda", "redução", "risco", "fraco",
    "abaixo do esperado", "decepção", "investigação", "multa",
    "processo", "endividamento", "rebaixamento", "crise",
]

_RSS_FEEDS = [
    "https://www.infomoney.com.br/feed/",
    "https://g1.globo.com/rss/g1/economia/",
]


def _fetch_news_raw() -> list:
    """Busca RSS gratuito — retorna lista de trechos de texto em minúsculas."""
    items = []
    for url in _RSS_FEEDS:
        try:
            req = _urllib_req.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urllib_req.urlopen(req, timeout=5) as resp:
                xml_data = resp.read().decode("utf-8", errors="ignore")
            root = _ET.fromstring(xml_data)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").lower()
                desc  = (item.findtext("description") or "").lower()
                items.append(title + " " + desc)
        except Exception:
            pass
    return items


def _score_news(asset: str, all_items: list) -> float:
    """Retorna sentimento de notícias: -1.0 a +1.0 para o ativo."""
    keywords = _ASSET_KEYWORDS.get(asset, [asset.lower()])
    relevant = [item for item in all_items if any(kw in item for kw in keywords)]
    if not relevant:
        return 0.0
    pos = sum(1 for item in relevant for w in _POS_WORDS if w in item)
    neg = sum(1 for item in relevant for w in _NEG_WORDS if w in item)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 3)


def _calc_rsi(prices: list, period: int = 14) -> float:
    """RSI usando suavização de Wilder."""
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 2)


def _calc_ema_series(prices: list, period: int) -> list:
    """Retorna série completa de EMA."""
    if not prices:
        return []
    period = min(period, len(prices))
    alpha = 2.0 / (period + 1)
    ema = prices[0]
    result = [ema]
    for p in prices[1:]:
        ema = p * alpha + ema * (1 - alpha)
        result.append(ema)
    return result


def _opportunity_score(prices: list, volumes: list, news_sentiment: float, preds_mini: dict) -> dict:
    """Calcula score de oportunidade 0-100 com breakdown por componente."""
    if len(prices) < 15:
        return {
            "score": 0, "rsi": 50.0, "ema_signal": "SEM_DADOS",
            "vol_ratio": 1.0, "news": 0.0, "breakdown": {},
        }
    rsi   = _calc_rsi(prices)
    ema9  = _calc_ema_series(prices, 9)
    ema21 = _calc_ema_series(prices, 21)

    # RSI score (0-25)
    if 35 <= rsi <= 55:    rsi_pts = 25
    elif 55 < rsi <= 65:   rsi_pts = 20
    elif 25 <= rsi < 35:   rsi_pts = 15
    elif 65 < rsi <= 75:   rsi_pts = 10
    else:                  rsi_pts = 5

    # EMA crossover score (0-25)
    e9, e21_v = ema9[-1], ema21[-1]
    e9_prev   = ema9[-min(4, len(ema9))]
    ema_rising = e9 > e9_prev
    if e9 > e21_v and ema_rising:
        ema_pts, ema_signal = 25, "ALTA_FORTE"
    elif e9 > e21_v:
        ema_pts, ema_signal = 18, "ALTA"
    elif e21_v > 0 and abs(e9 - e21_v) / e21_v < 0.002:
        ema_pts, ema_signal = 8, "NEUTRO"
    else:
        ema_pts, ema_signal = 2, "BAIXA"

    # Volume score (0-20)
    vol_ratio, vol_pts = 1.0, 5
    if len(volumes) >= 10:
        avg_vol   = sum(volumes[-10:]) / 10
        cur_vol   = volumes[-1]
        vol_ratio = round(cur_vol / avg_vol, 2) if avg_vol > 0 else 1.0
        if vol_ratio >= 2.0:    vol_pts = 20
        elif vol_ratio >= 1.5:  vol_pts = 15
        elif vol_ratio >= 1.2:  vol_pts = 10
        elif vol_ratio >= 1.0:  vol_pts = 5
        else:                   vol_pts = 2

    # Projeção alinhada (0-20)
    c1h = preds_mini.get("change_1h_pct", 0) or 0
    c1d = preds_mini.get("change_1d_pct", 0) or 0
    if c1h > 0 and c1d > 0:   pred_pts = 20
    elif c1h > 0:              pred_pts = 12
    elif c1d > 0:              pred_pts = 8
    else:                      pred_pts = 0

    # Notícias (0-10)
    if news_sentiment >= 0.5:     news_pts = 10
    elif news_sentiment >= 0.2:   news_pts = 7
    elif news_sentiment >= -0.1:  news_pts = 5
    elif news_sentiment >= -0.3:  news_pts = 2
    else:                         news_pts = 0

    total = rsi_pts + ema_pts + vol_pts + pred_pts + news_pts
    return {
        "score":      min(total, 100),
        "rsi":        rsi,
        "ema_signal": ema_signal,
        "vol_ratio":  vol_ratio,
        "news":       news_sentiment,
        "breakdown": {
            "rsi":        rsi_pts,
            "ema":        ema_pts,
            "volume":     vol_pts,
            "prediction": pred_pts,
            "news":       news_pts,
        },
    }


@app.get("/market/score")
async def get_market_score():
    """
    Calcula score de oportunidade 0-100 por ativo, ranqueado do maior para o menor.
    Combina: RSI + cruzamento EMA9/EMA21 + volume relativo + alinhamento de projeção + sentimento de notícias RSS.
    """
    if not MARKET_DATA_AVAILABLE:
        raise HTTPException(status_code=503, detail="Serviço de dados de mercado não disponível")
    try:
        assets = list(settings.ALL_ASSETS)

        # Busca sequencial para evitar contencao de semaforo
        klines_5m = await market_data_service.get_all_klines(assets, "5m", 50)
        klines_1d = await market_data_service.get_all_klines(assets, "1d", 35)

        # Notícias RSS (cache 10 min)
        global _news_cache
        now = _time_module.time()
        if now - _news_cache["ts"] > _NEWS_TTL:
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(None, _fetch_news_raw)
            sentiment_map = {a: _score_news(a, raw) for a in assets}
            _news_cache = {"data": sentiment_map, "ts": now}
        else:
            sentiment_map = _news_cache["data"]

        scored = {}
        for asset in assets:
            data5m    = klines_5m.get(asset, {})
            prices5m  = data5m.get("prices", [])
            volumes5m = data5m.get("volumes", [])
            prices1d  = klines_1d.get(asset, {}).get("prices", [])

            if len(prices5m) < 5:
                continue
            current = prices5m[-1]

            lr_1h, _ = _linear_regression_predict(prices5m, 12)
            ema_1h   = _ema_project(prices5m, 14, 12)
            pred_1h  = lr_1h * 0.6 + ema_1h * 0.4

            if len(prices1d) >= 5:
                lr_1d, _ = _linear_regression_predict(prices1d, 1)
                ema_1d   = _ema_project(prices1d, 14, 1)
                pred_1d  = lr_1d * 0.6 + ema_1d * 0.4
                c_1d     = prices1d[-1]
            else:
                lr_1d, _ = _linear_regression_predict(prices5m, 96)
                ema_1d   = _ema_project(prices5m, 14, 96)
                pred_1d  = lr_1d * 0.6 + ema_1d * 0.4
                c_1d     = current

            preds_mini = {
                "change_1h_pct": ((pred_1h - current) / current * 100) if current else 0,
                "change_1d_pct": ((pred_1d - c_1d) / c_1d * 100) if c_1d else 0,
            }

            result = _opportunity_score(prices5m, volumes5m, sentiment_map.get(asset, 0.0), preds_mini)
            result.update({
                "current":       round(current, 4),
                "pred_1h":       round(pred_1h, 4),
                "change_1h_pct": round(preds_mini["change_1h_pct"], 3),
                "pred_1d":       round(pred_1d, 4),
                "change_1d_pct": round(preds_mini["change_1d_pct"], 3),
                "news_found":    sentiment_map.get(asset, 0.0) != 0.0,
            })
            scored[asset] = result

        ranked = dict(sorted(scored.items(), key=lambda x: x[1]["score"], reverse=True))
        top3   = list(ranked.keys())[:3]

        return {
            "success":      True,
            "data":         ranked,
            "top3":         top3,
            "generated_at": datetime.now().isoformat(),
            "note":         "Score 0-100: RSI + EMA + volume + projeção + notícias. Não é recomendação financeira.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/market/klines/{asset}")
async def get_market_klines(asset: str, interval: str = "5m", limit: int = 25):
    """Obtém candles históricos da Binance"""
    if not MARKET_DATA_AVAILABLE:
        raise HTTPException(status_code=503, detail="Serviço de dados de mercado não disponível")
    try:
        klines = await market_data_service.get_klines(asset.upper(), interval, limit)
        if klines is None:
            raise HTTPException(status_code=404, detail=f"Ativo {asset} não encontrado")
        return {
            "success": True,
            "message": f"Klines de {asset.upper()} obtidos",
            "data": klines,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/market/analyze-live")
async def analyze_live_market(interval: str = "5m", limit: int = 25):
    """Análise completa com dados REAIS da Binance (substitui dados mock)"""
    if not MARKET_DATA_AVAILABLE:
        raise HTTPException(status_code=503, detail="Servico de dados de mercado nao disponivel")
    try:
        # 1. Buscar dados reais (crypto e fast assets)
        live_data = await market_data_service.get_all_klines(
            settings.ALL_ASSETS, interval, limit
        )
        # Fallback: se live_data vazio/poucas assets, merge com test_data
        source_data = dict(test_assets_data)  # start with test
        if live_data and len(live_data) > 0:
            source_data.update(live_data)  # override with live

        # 2. Analisar Momentum
        momentum_results = MomentumAnalyzer.calculate_multiple_assets(source_data)
        momentum_scores = {asset: data["momentum_score"] for asset, data in momentum_results.items()}

        # 3. Analisar Risco (usando BTC como referência)
        btc_data = source_data.get("BTC", {})
        risk_analysis = RiskAnalyzer.calculate_irq(
            btc_data.get("prices", []),
            btc_data.get("volumes", []),
        )
        irq_score = risk_analysis["irq_score"]
        protection = RiskAnalyzer.get_protection_level(irq_score)

        # 4. Calcular Alocação
        initial_capital = settings.INITIAL_CAPITAL
        allocation = PortfolioManager.calculate_portfolio_allocation(
            momentum_scores, irq_score, initial_capital,
        )
        rebalancing = PortfolioManager.apply_rebalancing_rules(
            allocation, momentum_results, initial_capital, irq_score,
        )
        risk_metrics = PortfolioManager.calculate_risk_metrics(allocation, initial_capital)

        # 5. Salvar análise no banco
        if DB_AVAILABLE:
            db.save_analysis("live_full", {
                "momentum": momentum_scores,
                "irq": irq_score,
                "allocation": {k: v for k, v in allocation.items()},
            }, irq_score)

        live_count = len(live_data) if live_data else 0
        return {
            "success": True,
            "message": f"Analise ao vivo ({live_count} live + {len(source_data)-live_count} test)",
            "source": "binance+yahoo+test",
            "data": {
                "timestamp": datetime.utcnow(),
                "interval": interval,
                "assets_analyzed": len(source_data),
                "momentum_analysis": {
                    asset: {
                        "momentum_score": momentum_results[asset]["momentum_score"],
                        "trend_status": momentum_results[asset]["trend_status"],
                        "classification": momentum_results[asset]["classification"],
                        "return_pct": momentum_results[asset]["return_pct"],
                    }
                    for asset in momentum_results
                },
                "risk_analysis": {
                    "irq_score": irq_score,
                    "level": protection["level"],
                    "protection_level": protection["level"],
                    "color": protection["color"],
                    "reduction_percentage": protection["reduction_percentage"],
                    "signal_scores": {
                        "S1": risk_analysis["s1_trend_loss"],
                        "S2": risk_analysis["s2_selling_pressure"],
                        "S3": risk_analysis["s3_volatility"],
                        "S4": risk_analysis["s4_rsi_divergence"],
                        "S5": risk_analysis["s5_losing_streak"],
                    },
                    "rsi": risk_analysis["rsi"],
                },
                "allocations": rebalancing,
                "portfolio_allocation": {
                    "allocation": {
                        asset: data["recommended_amount"]
                        for asset, data in rebalancing.items()
                    }
                },
                "risk_metrics": risk_metrics,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════
# ENDPOINTS: GERENCIAMENTO DE RISCO (STOP LOSS / LIMITES)
# ═══════════════════════════════════════════

@app.get("/risk/status")
async def risk_status():
    """Status do gerenciador de risco (stop loss, limites, P&L diário) — sincronizado com trade"""
    status = risk_manager.get_status()
    # Enriquecer com dados do trade state
    status["trade_total_pnl"] = _trade_state.get("total_pnl", 0.0)
    status["trade_capital"] = _trade_state.get("capital", settings.INITIAL_CAPITAL)
    status["trade_positions_count"] = len(_trade_state.get("positions", {}))
    return {
        "success": True,
        "message": "Status de risco operacional",
        "data": status,
    }


@app.post("/risk/check-stop-loss")
async def check_stop_loss_all():
    """Verifica stop loss/take profit de todas as posições com preços atuais"""
    try:
        # Obter preços atuais
        if MARKET_DATA_AVAILABLE:
            current_prices = await market_data_service.get_all_prices()
        else:
            # Usar último preço dos dados mock
            current_prices = {
                asset: data["prices"][-1]
                for asset, data in test_assets_data.items()
            }

        alerts = risk_manager.check_all_positions(current_prices)
        return {
            "success": True,
            "message": f"{len(alerts)} alertas de stop loss/take profit",
            "data": {
                "alerts": alerts,
                "positions_checked": len(risk_manager.positions),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/risk/can-trade")
async def can_trade():
    """Verifica se é permitido operar (limites diários, perda máxima, etc)"""
    allowed, reason = risk_manager.can_trade()
    return {
        "success": True,
        "data": {
            "allowed": allowed,
            "reason": reason,
            "daily_check": risk_manager.check_daily_loss_limit(),
            "trade_limits": risk_manager.check_trade_limits(),
        },
    }


# ═══════════════════════════════════════════
# ENDPOINTS: BANCO DE DADOS
# ═══════════════════════════════════════════

@app.get("/db/stats")
async def database_stats():
    """Estatísticas do banco de dados"""
    if not DB_AVAILABLE:
        return {"success": False, "message": "Banco de dados não disponível"}
    return {
        "success": True,
        "message": "Estatísticas do banco",
        "data": db.get_stats(),
    }


@app.get("/db/trades")
async def get_trades(limit: int = 50, asset: str = None):
    """Histórico de trades"""
    if not DB_AVAILABLE:
        return {"success": False, "message": "Banco de dados não disponível", "data": []}
    return {
        "success": True,
        "data": db.get_trades(limit, asset),
    }


@app.get("/db/analysis-history")
async def get_analysis_history(limit: int = 20):
    """Histórico de análises salvas"""
    if not DB_AVAILABLE:
        return {"success": False, "message": "Banco de dados não disponível", "data": []}
    return {
        "success": True,
        "data": db.get_analysis_history(limit=limit),
    }


# ═══════════════════════════════════════════
# ENDPOINT: MÓDULOS DISPONÍVEIS
# ═══════════════════════════════════════════

@app.get("/modules")
async def list_modules():
    """Lista todos os módulos e seu status"""
    return {
        "success": True,
        "data": {
            "engines": {
                "momentum": True,
                "risk_irq": True,
                "portfolio": True,
                "risk_manager": True,
            },
            "integrations": {
                "binance_market_data": MARKET_DATA_AVAILABLE,
                "alerts_telegram_discord": ALERTS_AVAILABLE,
                "ml_predictions": ML_AVAILABLE,
                "database": DB_AVAILABLE,
            },
            "config": {
                "initial_capital": settings.INITIAL_CAPITAL,
                "stop_loss": f"{settings.STOP_LOSS_PERCENTAGE*100:.0f}%",
                "take_profit": f"{settings.TAKE_PROFIT_PERCENTAGE*100:.0f}%",
                "max_daily_loss": f"{settings.MAX_DAILY_LOSS_PERCENTAGE*100:.0f}%",
                "max_trades_hour": settings.MAX_TRADES_PER_HOUR,
                "max_trades_day": settings.MAX_TRADES_PER_DAY,
                "allowed_assets": settings.ALLOWED_ASSETS,
            },
        },
    }


# ═══════════════════════════════════════════
# TRADE STATE — estado persistido em disco
# ═══════════════════════════════════════════

_DATA_DIR   = Path(__file__).parent.parent / "data"
_STATE_FILE = _DATA_DIR / "trade_state.json"
_PERF_FILE  = _DATA_DIR / "performance.json"

def _ensure_data_dir():
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

def _load_json(path: Path, default: dict) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save_json(path: Path, obj: dict):
    _ensure_data_dir()
    try:
        path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass

# ── trade state ────────────────────────────────────────
_DEFAULT_TRADE_STATE: dict = {
    "capital": settings.INITIAL_CAPITAL,
    "auto_trading": True,
    "positions": {},       # asset -> {"amount": float, "action": str, "pct": float}
    "last_no_position_reason": "",
    "log": [],             # lista de eventos {timestamp, type, asset, amount, note}
    "total_pnl": 0.0,
    "last_cycle": None,
}
_trade_state: dict = db_state.load_state("trade_state", dict(_DEFAULT_TRADE_STATE))
# Corrige valores corrompidos do bug antigo de P&L
if _trade_state.get("total_pnl", 0.0) < -5:
    _trade_state["total_pnl"] = 0.0
    db_state.save_state("trade_state", _trade_state)
# NOTA: NÃO sobrescrever capital salvo — respeitar o estado real do bot.
# O default já cobre o primeiro boot (load_state usa _DEFAULT_TRADE_STATE quando não há estado).


# ── performance history ────────────────────────────────
_DEFAULT_PERF: dict = {
    "cycles": [],          # [{timestamp, pnl, capital, irq, wins, losses}]
    "total_pnl_history": [],
    "win_count": 0,
    "loss_count": 0,
    "best_day_pnl": 0.0,
    "worst_day_pnl": 0.0,
    "last_backtest": None,
    "total_gain": 0.0,     # soma acumulada de todos os ciclos positivos
    "total_loss": 0.0,     # soma acumulada do absoluto de ciclos negativos
    "total_fees": 0.0,
    "total_brokerage": 0.0,
    "total_exchange_fees": 0.0,
    "total_spread": 0.0,
    "total_slippage": 0.0,
    "total_fx": 0.0,
    "total_min_fee_adj": 0.0,
}
_perf_state: dict = db_state.load_state("performance", dict(_DEFAULT_PERF))


def _brt_now():
    """Retorna datetime atual em BRT (UTC-3) — consistente em qualquer servidor."""
    from datetime import timezone as _tz, timedelta as _td
    return datetime.now(_tz(_td(hours=-3)))


def _trade_log(event_type: str, asset: str, amount: float, note: str):
    """Insere um evento no log de trading (máx 200 entradas) e persiste em disco."""
    _trade_state["log"].insert(0, {
        "timestamp": _brt_now().isoformat(),
        "type": event_type,
        "asset": asset,
        "amount": round(amount, 2),
        "note": note,
    })
    if len(_trade_state["log"]) > 200:
        _trade_state["log"] = _trade_state["log"][:200]
    db_state.save_state("trade_state", _trade_state)


# Flag de segurança: garantir que o primeiro save não sobrescreva dados do DB
_perf_db_safety_checked: bool = False


def _record_cycle_performance(pnl: float, capital: float, irq: float,
                               pnl_5m: float = 0.0, pnl_1h: float = 0.0, pnl_1d: float = 0.0,
                               costs: dict = None,
                               pnl_mr: float = 0.0, pnl_bo: float = 0.0, pnl_sq: float = 0.0,
                               pnl_ls: float = 0.0, pnl_fvg: float = 0.0,
                               pnl_vr: float = 0.0, pnl_pb: float = 0.0):
    """Registra o P&L de um ciclo no histórico de performance."""
    global _perf_db_safety_checked, _perf_state
    # ── SAFETY MERGE: no primeiro ciclo após startup, verifica se o DB tem mais ciclos ──
    # Isso previne sobrescrever histórico válido quando o estado em memória começou vazio
    if not _perf_db_safety_checked:
        _perf_db_safety_checked = True
        try:
            db_saved = db_state.load_state("performance", {})
            db_cycles_count = len(db_saved.get("cycles", []))
            mem_cycles_count = len(_perf_state.get("cycles", []))
            if db_cycles_count > mem_cycles_count:
                print(
                    f"[perf] SAFETY MERGE: DB tem {db_cycles_count} ciclos, memória tem {mem_cycles_count}. "
                    f"Restaurando histórico do DB antes de salvar.",
                    flush=True,
                )
                _perf_state.update(db_saved)
            else:
                print(f"[perf] Safety check OK: memória={mem_cycles_count} ciclos, DB={db_cycles_count}", flush=True)
        except Exception as _se:
            print(f"[perf] Aviso no safety merge: {_se}", flush=True)
    cst = costs or {}
    _perf_state["cycles"].append({
        "timestamp": _brt_now().isoformat(),
        "pnl":       round(pnl, 4),
        "pnl_5m":    round(pnl_5m, 4),
        "pnl_1h":    round(pnl_1h, 4),
        "pnl_1d":    round(pnl_1d, 4),
        "pnl_mr":    round(pnl_mr, 4),
        "pnl_bo":    round(pnl_bo, 4),
        "pnl_sq":    round(pnl_sq, 4),
        "pnl_ls":    round(pnl_ls, 4),
        "pnl_fvg":   round(pnl_fvg, 4),
        "pnl_vr":    round(pnl_vr, 4),
        "pnl_pb":    round(pnl_pb, 4),
        "fees_total": round(cst.get("total", 0.0), 6),
        "fees_brokerage": round(cst.get("brokerage", 0.0), 6),
        "fees_exchange": round(cst.get("exchange_fees", 0.0), 6),
        "fees_spread": round(cst.get("spread", 0.0), 6),
        "fees_slippage": round(cst.get("slippage", 0.0), 6),
        "fees_fx": round(cst.get("fx", 0.0), 6),
        "fees_min_adj": round(cst.get("min_fee_adj", 0.0), 6),
        "capital":   round(capital, 2),
        "irq":       round(irq, 4),
    })
    # manter máx 500 ciclos
    if len(_perf_state["cycles"]) > 500:
        _perf_state["cycles"] = _perf_state["cycles"][-500:]

    _perf_state["total_pnl_history"].append(round(capital + pnl, 2))
    if len(_perf_state["total_pnl_history"]) > 500:
        _perf_state["total_pnl_history"] = _perf_state["total_pnl_history"][-500:]

    if pnl > 0:
        _perf_state["win_count"] = _perf_state.get("win_count", 0) + 1
        if pnl > _perf_state.get("best_day_pnl", 0.0):
            _perf_state["best_day_pnl"] = round(pnl, 4)
        _perf_state["total_gain"] = round(_perf_state.get("total_gain", 0.0) + pnl, 4)
    elif pnl < 0:
        _perf_state["loss_count"] = _perf_state.get("loss_count", 0) + 1
        if pnl < _perf_state.get("worst_day_pnl", 0.0):
            _perf_state["worst_day_pnl"] = round(pnl, 4)
        _perf_state["total_loss"] = round(_perf_state.get("total_loss", 0.0) + abs(pnl), 4)

    _perf_state["total_fees"] = round(_perf_state.get("total_fees", 0.0) + cst.get("total", 0.0), 6)
    _perf_state["total_brokerage"] = round(_perf_state.get("total_brokerage", 0.0) + cst.get("brokerage", 0.0), 6)
    _perf_state["total_exchange_fees"] = round(_perf_state.get("total_exchange_fees", 0.0) + cst.get("exchange_fees", 0.0), 6)
    _perf_state["total_spread"] = round(_perf_state.get("total_spread", 0.0) + cst.get("spread", 0.0), 6)
    _perf_state["total_slippage"] = round(_perf_state.get("total_slippage", 0.0) + cst.get("slippage", 0.0), 6)
    _perf_state["total_fx"] = round(_perf_state.get("total_fx", 0.0) + cst.get("fx", 0.0), 6)
    _perf_state["total_min_fee_adj"] = round(_perf_state.get("total_min_fee_adj", 0.0) + cst.get("min_fee_adj", 0.0), 6)

    # SAFETY: nunca sobrescrever DB se ele tiver mais ciclos (previne perda de histórico)
    try:
        db_current = db_state.load_state("performance", {})
        db_cycle_count = len(db_current.get("cycles", []))
        mem_cycle_count = len(_perf_state.get("cycles", []))
        if db_cycle_count > mem_cycle_count + 1:  # margem de 1 para o ciclo que acabou de rodar
            print(f"[perf] BLOQUEADO: DB tem {db_cycle_count} ciclos, mem tem {mem_cycle_count}. "
                  f"Não sobrescrevendo!", flush=True)
            # Restaurar do DB e adicionar o ciclo atual
            _perf_state.update(db_current)
            _perf_state["cycles"].append({
                "timestamp": _brt_now().isoformat(),
                "pnl": round(pnl, 4), "capital": round(capital, 2), "irq": round(irq, 4),
                "pnl_5m": round(pnl_5m, 4), "pnl_1h": round(pnl_1h, 4), "pnl_1d": round(pnl_1d, 4),
                "fees_total": round(cst.get("total", 0.0), 6),
            })
    except Exception as _safe_e:
        print(f"[perf] Aviso no safety check: {_safe_e}", flush=True)

    db_state.save_state("performance", _perf_state)


@app.get("/trade/status")
async def trade_status():
    """Retorna o estado atual do trading: capital, posições, log de eventos."""
    _, session_label = _current_session()
    # Capital efetivo = capital base + ganho/perda acumulado do dia (BRT)
    from datetime import timezone as _tz, timedelta as _td
    _brt = _tz(_td(hours=-3))
    today_str = datetime.now(_brt).strftime("%Y-%m-%d")
    today_cycles = [c for c in _perf_state.get("cycles", []) if c.get("timestamp", "").startswith(today_str)]
    pnl_today_live = round(sum(c.get("pnl", 0) for c in today_cycles), 2)
    # Subtrai baseline do dia (ganhos com capital anterior ao reset)
    # Só aplica baseline se foi definido no mesmo dia de hoje (não persiste entre dias)
    pnl_today_baseline = 0.0
    if _trade_state.get("pnl_today_baseline_date") == today_str:
        pnl_today_baseline = _trade_state.get("pnl_today_baseline", 0.0)
    pnl_today_live = round(pnl_today_live - pnl_today_baseline, 2)
    capital_base = _trade_state.get("capital", settings.INITIAL_CAPITAL)
    capital_efetivo = round(capital_base + pnl_today_live, 2)

    # ── Capital Split BRL vs USD ─────────────────────────────────────────
    # Tenta cotação live do USD/BRL via brokers integrados (AV → Yahoo → fallback)
    usd_rate = settings.USD_BRL_RATE
    try:
        if market_data_service:
            live_rate = await market_data_service.get_usd_brl_rate()
            if live_rate and live_rate > 0:
                usd_rate = round(live_rate, 4)
    except Exception:
        pass

    capital_brl      = round(capital_efetivo * settings.CAPITAL_BRL_PCT, 2)   # ex: 40% em R$
    capital_usd_brl  = round(capital_efetivo * settings.CAPITAL_USD_PCT, 2)   # ex: 60% ainda em R$
    capital_usd      = round(capital_usd_brl / usd_rate, 2)                   # convertido para USD

    return {
        "success": True,
        "data": {
            "capital":          capital_base,
            "capital_efetivo":  capital_efetivo,
            "pnl_hoje":         pnl_today_live,
            "auto_trading":     _trade_state.get("auto_trading", True),
            "total_pnl":        round(_trade_state.get("total_pnl", 0.0) - _trade_state.get("total_pnl_baseline", 0.0), 4),
            "positions":        _trade_state.get("positions", []),
            "last_no_position_reason": _trade_state.get("last_no_position_reason", ""),
            "log":              _trade_state.get("log", []),
            "last_cycle":       _trade_state.get("last_cycle", None),
            "b3_open":          _is_market_open(),
            "session":          session_label,
            # ── Bolsões BRL / USD ──────────────────────────────────────
            "capital_brl":      capital_brl,       # parcela B3 em R$
            "capital_usd_brl":  capital_usd_brl,   # parcela crypto/US em R$
            "capital_usd":      capital_usd,        # parcela crypto/US em USD
            "usd_rate":         usd_rate,           # R$/USD usado na conversão
            # ── Broker info ────────────────────────────────────────────
            "trading_mode":     getattr(settings, "TRADING_MODE", "paper"),
            "broker_status":    market_data_service.broker_status() if market_data_service else {},
            "strategy": {
                "regime": _strategy_state.get("regime", "normal"),
                "dynamic_alloc_enabled": _strategy_state.get("dynamic_alloc_enabled", True),
                "regime_filter_enabled": _strategy_state.get("regime_filter_enabled", True),
                "tf_risk_tuning_enabled": _strategy_state.get("tf_risk_tuning_enabled", True),
                "last_reason": _strategy_state.get("last_reason", "base"),
                "alloc_base_pct": {
                    "5m": round(_TIMEFRAME_ALLOC["5m"] * 100, 1),
                    "1h": round(_TIMEFRAME_ALLOC["1h"] * 100, 1),
                    "1d": round(_TIMEFRAME_ALLOC["1d"] * 100, 1),
                },
                "alloc_effective_pct": {
                    "5m": round(_strategy_state.get("last_effective_alloc", _TIMEFRAME_ALLOC).get("5m", 0) * 100, 1),
                    "1h": round(_strategy_state.get("last_effective_alloc", _TIMEFRAME_ALLOC).get("1h", 0) * 100, 1),
                    "1d": round(_strategy_state.get("last_effective_alloc", _TIMEFRAME_ALLOC).get("1d", 0) * 100, 1),
                },
                "recent_tf_pnl": _strategy_state.get("last_recent_tf_pnl", {"5m": 0.0, "1h": 0.0, "1d": 0.0}),
            },
        },
    }


@app.post("/trade/capital")
async def set_trade_capital(body: dict):
    """Deposita ou atualiza o capital do bot."""
    amount = float(body.get("amount", 0))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Valor deve ser positivo")
    prev = _trade_state["capital"]
    _trade_state["capital"] = amount
    settings.INITIAL_CAPITAL = amount
    delta = amount - prev
    event = "DEPÓSITO" if delta >= 0 else "RETIRADA"
    # Ao mudar capital, registra baseline do PnL de hoje (ganhos do capital anterior)
    from datetime import timezone as _tz2, timedelta as _td2
    _brt2 = _tz2(_td2(hours=-3))
    today_str2 = datetime.now(_brt2).strftime("%Y-%m-%d")
    today_cycles2 = [c for c in _perf_state.get("cycles", []) if c.get("timestamp", "").startswith(today_str2)]
    current_pnl_today = round(sum(c.get("pnl", 0) for c in today_cycles2), 2)
    _trade_state["pnl_today_baseline"] = current_pnl_today
    _trade_state["pnl_today_baseline_date"] = today_str2
    _trade_state["total_pnl_baseline"] = _trade_state.get("total_pnl", 0.0)
    _trade_log(event, "—", abs(delta), f"Capital {event.lower()} de R$ {prev:.2f} → R$ {amount:.2f} | PnL baseline fixado em R$ {current_pnl_today:.2f}")
    # Persiste imediatamente no banco para sobreviver a deploys/restarts
    db_state.save_state("trade_state", _trade_state)
    return {"success": True, "capital": amount, "previous": prev, "pnl_baseline": current_pnl_today}


@app.post("/trade/start")
async def start_auto_trading():
    """Ativa o trading automático."""
    _trade_state["auto_trading"] = True
    _trade_log("SISTEMA", "—", 0, "✅ Trading automático INICIADO")
    return {"success": True, "auto_trading": True}


@app.post("/trade/reset-pnl-baseline")
async def reset_pnl_baseline(request: Request):
    """Fixa o PnL atual do dia como baseline — próximos ganhos partem de zero.
    Útil quando o capital é resetado no meio do dia para nova simulação.
    Parâmetro opcional JSON: {"total_pnl_baseline_override": 1971.83}"""
    from datetime import timezone as _tz3, timedelta as _td3
    _brt3 = _tz3(_td3(hours=-3))
    today_str3 = datetime.now(_brt3).strftime("%Y-%m-%d")
    today_cycles3 = [c for c in _perf_state.get("cycles", []) if c.get("timestamp", "").startswith(today_str3)]
    current_pnl = round(sum(c.get("pnl", 0) for c in today_cycles3), 2)
    _trade_state["pnl_today_baseline"] = current_pnl
    _trade_state["pnl_today_baseline_date"] = today_str3
    # Permite sobrescrever o baseline de total_pnl via parâmetro JSON opcional
    try:
        body = await request.json()
        override = body.get("total_pnl_baseline_override")
    except Exception:
        override = None
    if override is not None:
        _trade_state["total_pnl_baseline"] = float(override)
    else:
        _trade_state["total_pnl_baseline"] = _trade_state.get("total_pnl", 0.0)
    db_state.save_state("trade_state", _trade_state)
    _trade_log("SISTEMA", "—", 0, f"🔄 PnL baseline fixado em R$ {current_pnl:.2f} — nova simulação R$ {_trade_state.get('capital', 0):.2f}")
    return {"success": True, "pnl_baseline_set": current_pnl, "total_pnl_baseline": _trade_state["total_pnl_baseline"], "capital": _trade_state.get("capital", 0)}


@app.post("/trade/stop")
async def stop_auto_trading():
    """Pausa o trading automático."""
    _trade_state["auto_trading"] = False
    _trade_log("SISTEMA", "—", 0, "⏸ Trading automático PAUSADO")
    return {"success": True, "auto_trading": False}


@app.post("/trade/reset")
async def reset_trade_state(request: Request):
    """Zera o histórico de P&L, ciclos e restaura capital ao valor padrão."""
    _trade_state["total_pnl"] = 0.0
    _trade_state["capital"]   = settings.INITIAL_CAPITAL
    _trade_state["log"]       = []
    _trade_state["positions"] = {}
    db_state.save_state("trade_state", _trade_state)
    _perf_state["cycles"] = []
    _perf_state["total_pnl_history"] = []
    _perf_state["win_count"] = 0
    _perf_state["loss_count"] = 0
    _perf_state["best_day_pnl"] = 0.0
    _perf_state["worst_day_pnl"] = 0.0
    _perf_state["total_fees"] = 0.0
    _perf_state["total_brokerage"] = 0.0
    _perf_state["total_exchange_fees"] = 0.0
    _perf_state["total_spread"] = 0.0
    _perf_state["total_slippage"] = 0.0
    _perf_state["total_fx"] = 0.0
    _perf_state["total_min_fee_adj"] = 0.0
    db_state.save_state("performance", _perf_state)
    # Reset proteção inteligente também
    _protection_state["paused"] = False
    _protection_state["hard_stopped"] = False
    _protection_state["consecutive_losses"] = 0
    _protection_state["size_multiplier"] = 1.0
    _protection_state["peak_capital"] = settings.INITIAL_CAPITAL
    _protection_state["pause_reason"] = ""
    _protection_state["trailing_highs"] = {}
    return {"success": True, "message": f"Histórico zerado — capital restaurado para R$ {settings.INITIAL_CAPITAL:.2f}. Proteções resetadas."}


@app.post("/admin/restore-perf")
async def admin_restore_perf(request: Request, payload: dict):
    """
    Restaura manualmente o estado de performance (para recuperação de histórico perdido).
    Aceita campos parciais — apenas os fornecidos são atualizados.
    Campos aceitos: cycles, win_count, loss_count, best_day_pnl, worst_day_pnl,
                    total_gain, total_loss, total_fees, total_pnl_history
    """
    global _perf_state, _perf_db_safety_checked
    allowed = {
        "cycles", "win_count", "loss_count", "best_day_pnl", "worst_day_pnl",
        "total_gain", "total_loss", "total_fees", "total_brokerage",
        "total_exchange_fees", "total_spread", "total_slippage", "total_fx",
        "total_min_fee_adj", "total_pnl_history",
        # Offsets históricos (para restaurar após reset/migração)
        "total_cycles_offset", "total_pnl_offset",
    }
    updated = []
    for key, val in payload.items():
        if key in allowed:
            _perf_state[key] = val
            updated.append(key)
    # Marca safety check como feito para não sobrescrever na próxima gravação
    _perf_db_safety_checked = True
    db_state.save_state("performance", _perf_state)
    cycles_count = len(_perf_state.get("cycles", []))
    print(f"[admin] restore-perf: {len(updated)} campos restaurados, {cycles_count} ciclos no estado", flush=True)
    return {
        "success": True,
        "updated_fields": updated,
        "cycles_in_state": cycles_count,
        "win_count": _perf_state.get("win_count", 0),
        "loss_count": _perf_state.get("loss_count", 0),
        "total_gain": _perf_state.get("total_gain", 0),
        "total_loss": _perf_state.get("total_loss", 0),
    }


@app.post("/admin/restore-trade")
async def admin_restore_trade(request: Request, payload: dict):
    """
    Restaura manualmente campos do trade_state (capital, total_pnl, etc).
    Campos aceitos: capital, total_pnl, auto_trading
    """
    allowed = {"capital", "total_pnl", "auto_trading"}
    updated = []
    for key, val in payload.items():
        if key in allowed:
            _trade_state[key] = val
            updated.append(key)
    if "capital" in payload:
        settings.INITIAL_CAPITAL = float(payload["capital"])
    db_state.save_state("trade_state", _trade_state)
    return {
        "success": True,
        "updated_fields": updated,
        "capital": _trade_state.get("capital"),
        "total_pnl": _trade_state.get("total_pnl"),
    }


@app.post("/trade/unfreeze")
async def unfreeze_bot(request: Request):
    """Desbloqueia o bot após Hard Stop (drawdown máximo). Reseta proteção mas mantém capital atual."""
    was_stopped = _protection_state["hard_stopped"]
    _protection_state["paused"] = False
    _protection_state["hard_stopped"] = False
    _protection_state["consecutive_losses"] = 0
    _protection_state["size_multiplier"] = 1.0
    _protection_state["peak_capital"] = _trade_state["capital"]  # novo pico = capital atual
    _protection_state["pause_reason"] = ""
    _protection_state["trailing_highs"] = {}
    status = "desbloqueado" if was_stopped else "já estava ativo"
    _trade_log("UNFREEZE", "—", _trade_state["capital"],
        f"🔓 Bot {status}. Novo pico: R$ {_trade_state['capital']:.2f}")
    return {
        "success": True,
        "was_frozen": was_stopped,
        "message": f"Bot {status}. Capital atual: R$ {_trade_state['capital']:.2f}. Proteções resetadas.",
    }


@app.get("/trade/sim-mode")
async def get_sim_mode():
    """Retorna o modo de simulação atual e os parâmetros de cada modo."""
    return {
        "success": True,
        "current_mode": _sim_mode,
        "profiles": {
            name: {
                "rejection_rate_pct":   round(p["rejection_rate"] * 100, 0),
                "capture_mean_pct":     round(p["capture_mean"]   * 100, 0),
                "loss_worsen_mean_pct": round((p["loss_worsen_mean"] - 1) * 100, 0),
                "cost_multiplier":      p["cost_multiplier"],
                "description": {
                    "normal":  "Dia médio de mercado. Referência para operação normal.",
                    "stress":  "Dia ruim: alta volatilidade, spreads 2.5×, 20% das ordens rejeitadas.",
                    "extreme": "Crise / black swan: spreads 5×, 35% rejeição, captura só 45% do movimento.",
                }.get(name, ""),
            }
            for name, p in _SIM_PROFILES.items()
        },
    }


@app.post("/trade/sim-mode")
async def set_sim_mode(body: dict):
    """
    Altera o modo de simulação de stress.

    Body: {"mode": "normal" | "stress" | "extreme"}

    Efeito imediato: próximo ciclo já usa os novos parâmetros.
    """
    global _sim_mode
    mode = (body.get("mode") or "").lower().strip()
    if mode not in _SIM_PROFILES:
        raise HTTPException(status_code=400,
            detail=f"Modo inválido. Use: {list(_SIM_PROFILES.keys())}")
    old_mode = _sim_mode
    _sim_mode = mode
    _sp = _SIM_PROFILES[mode]
    _trade_log("SIM_MODE", "—", 0,
        f"🎮 Modo de simulação alterado: {old_mode} → {mode} "
        f"| rejeição={_sp['rejection_rate']*100:.0f}% "
        f"| captura={_sp['capture_mean']*100:.0f}% "
        f"| custos={_sp['cost_multiplier']}x")
    return {
        "success": True,
        "previous_mode": old_mode,
        "current_mode":  mode,
        "params": {
            "rejection_rate_pct":   round(_sp["rejection_rate"] * 100, 0),
            "capture_mean_pct":     round(_sp["capture_mean"]   * 100, 0),
            "loss_worsen_mean_pct": round((_sp["loss_worsen_mean"] - 1) * 100, 0),
            "cost_multiplier":      _sp["cost_multiplier"],
        },
    }


@app.get("/trade/protection")
async def get_protection_status():
    """Retorna o estado atual do sistema de proteção inteligente."""
    capital = _trade_state["capital"]
    peak = _protection_state["peak_capital"]
    dd = ((peak - capital) / peak * 100) if peak > 0 else 0
    return {
        "paused": _protection_state["paused"],
        "hard_stopped": _protection_state["hard_stopped"],
        "pause_reason": _protection_state["pause_reason"],
        "consecutive_losses": _protection_state["consecutive_losses"],
        "size_multiplier": _protection_state["size_multiplier"],
        "peak_capital": round(peak, 2),
        "current_capital": round(capital, 2),
        "drawdown_pct": round(dd, 2),
        "drawdown_limit_pct": settings.MAX_DRAWDOWN_PERCENTAGE * 100,
        "daily_loss_limit_pct": settings.MAX_DAILY_LOSS_PERCENTAGE * 100,
        "weekly_loss_limit_pct": settings.MAX_WEEKLY_LOSS_PERCENTAGE * 100,
        "resume_momentum": settings.RESUME_MOMENTUM_THRESHOLD,
        "trailing_stop_pct": settings.TRAILING_STOP_PERCENTAGE * 100,
    }


@app.get("/trade/strategies")
async def trade_strategies():
    """Retorna status de todas as estratégias ativas com parâmetros."""
    return {
        "success": True,
        "data": {
            "atr_adaptive_sl_tp": {
                "active": True,
                "atr_period": settings.ATR_PERIOD,
                "sl_multiplier": settings.ATR_SL_MULTIPLIER,
                "tp_multiplier": settings.ATR_TP_MULTIPLIER,
                "sl_range": f"{settings.ATR_MIN_SL*100:.1f}%-{settings.ATR_MAX_SL*100:.1f}%",
                "cached_atr": {k: v for k, v in list(_atr_cache.items())[:10]},
            },
            "grid_trading": {
                "active": settings.GRID_ENABLED,
                "levels": settings.GRID_LEVELS,
                "spacing_pct": settings.GRID_SPACING_PCT * 100,
                "capital_pct": settings.GRID_CAPITAL_PCT * 100,
            },
            "scalping_turbo": {
                "active": settings.TURBO_ENABLED,
                "vol_threshold": settings.TURBO_VOL_THRESHOLD * 100,
                "cycle_seconds": settings.TURBO_CYCLE_SECONDS,
                "tp_pct": settings.TURBO_TP_PCT * 100,
            },
            "volume_confirmation": {
                "active": settings.VOLUME_CONFIRM_ENABLED,
                "confirm_mult": settings.VOLUME_CONFIRM_MULTIPLIER,
                "reject_mult": settings.VOLUME_REJECT_MULTIPLIER,
            },
            "partial_take_profit": {
                "active": settings.PARTIAL_TP_ENABLED,
                "first_pct": settings.PARTIAL_TP_FIRST_PCT * 100,
                "first_target": settings.PARTIAL_TP_FIRST_TARGET * 100,
            },
            "momentum_acceleration": {
                "active": settings.MOMENTUM_ACCEL_ENABLED,
                "threshold": settings.MOMENTUM_ACCEL_THRESHOLD * 100,
                "boost": settings.MOMENTUM_ACCEL_BOOST,
            },
            # Estratégias anteriores
            "kelly_criterion": {"active": True, "fraction": settings.KELLY_FRACTION},
            "mean_reversion": {"active": True, "drop_trigger": "3%"},
            "dca_intelligent": {"active": True, "dca_boost": "50%"},
            "sentiment_analysis": {"active": True},
            "cross_asset_momentum": {"active": True},
            "orderbook_analysis": {"active": True},
            "trailing_stop": {"active": True, "pct": settings.TRAILING_STOP_PERCENTAGE * 100},
            "smart_protection": {"active": True, "daily_limit": settings.MAX_DAILY_LOSS_PERCENTAGE * 100,
                                 "weekly_limit": settings.MAX_WEEKLY_LOSS_PERCENTAGE * 100,
                                 "drawdown_floor": settings.MAX_DRAWDOWN_PERCENTAGE * 100},
        },
    }


@app.post("/trade/cycle")
async def run_trade_cycle(request: Request):
    """
    Executa um ciclo de análise e simula as ordens que o bot colocaria.
    Registra cada decisão no log de trading.
    """
    try:
        result = await asyncio.wait_for(
            _run_trade_cycle_internal(),
            timeout=180
        )
        try:
            db_state.save_state("trade_state", _trade_state)
            db_state.save_state("performance", _perf_state)
        except Exception:
            pass
        return {
            "success": True,
            "data": result,
        }
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[trade/cycle] ❌ Erro: {e}\n{tb}", flush=True)
        raise HTTPException(status_code=500, detail=f"Cycle error: {str(e)[:300]}")


# ═══════════════════════════════════════════
# ESTRATÉGIAS AVANÇADAS
# ═══════════════════════════════════════════

def _sentiment_boost(asset: str) -> float:
    """
    Analisa sentimento via CryptoPanic/RSS (simplificado).
    Retorna multiplicador: >1 = positivo, <1 = negativo, 1 = neutro.
    """
    try:
        import requests as _req
        # CryptoPanic API gratuita para crypto
        if asset in settings.CRYPTO_ASSETS:
            url = f"https://cryptopanic.com/api/v1/posts/?auth_token=free&currencies={asset}&kind=news&filter=important"
            resp = _req.get(url, timeout=3)
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if not results:
                    return 1.0
                # Contar positivos vs negativos
                pos = sum(1 for r in results[:5] if r.get("votes", {}).get("positive", 0) > 0)
                neg = sum(1 for r in results[:5] if r.get("votes", {}).get("negative", 0) > 0)
                if pos > neg:
                    return 1.15  # +15% de peso
                elif neg > pos:
                    return 0.85  # -15% de peso
    except Exception:
        pass
    return 1.0


def _cross_asset_momentum(klines: dict) -> dict:
    """
    Detecta momentum cruzado: se BTC sobe forte, altcoins seguem.
    Retorna boost multiplicador por ativo.
    """
    boosts = {}
    btc_data = klines.get("BTC", {})
    btc_prices = btc_data.get("prices", [])
    if len(btc_prices) >= 3:
        btc_move = (btc_prices[-1] - btc_prices[-3]) / btc_prices[-3] if btc_prices[-3] > 0 else 0
        if btc_move > 0.005:  # BTC subiu > 0.5% em 3 candles
            for asset in klines:
                if asset != "BTC" and asset in settings.CRYPTO_ASSETS:
                    boosts[asset] = 1.10  # +10% para altcoins
        elif btc_move < -0.005:  # BTC caiu forte
            for asset in klines:
                if asset != "BTC" and asset in settings.CRYPTO_ASSETS:
                    boosts[asset] = 0.90  # -10% para altcoins
    return boosts


def _orderbook_signal(asset: str) -> float:
    """
    Analisa profundidade de ordem (simplificado via Binance API).
    Retorna multiplicador: >1 = mais bids que asks, <1 = mais asks.
    """
    try:
        if asset not in settings.CRYPTO_ASSETS:
            return 1.0
        symbol = f"{asset}USDT"
        import requests as _req
        resp = _req.get(f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit=20", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            total_bids = sum(float(b[1]) for b in data.get("bids", []))
            total_asks = sum(float(a[1]) for a in data.get("asks", []))
            if total_asks > 0:
                ratio = total_bids / total_asks
                if ratio > 1.5:    return 1.10   # muita demanda
                elif ratio < 0.6:  return 0.90   # muita oferta
    except Exception:
        pass
    return 1.0


# Cache para não chamar sentimento/orderbook a cada ciclo (a cada 5 min)
_signal_cache: dict = {"ts": "", "sentiment": {}, "orderbook": {}, "cross_mom": {}}

# ═══════════════════════════════════════════
# PROTEÇÃO INTELIGENTE (pausa/retorno automático)
# ═══════════════════════════════════════════
_protection_state: dict = {
    "paused": False,                # bot está em pausa?
    "pause_reason": "",             # motivo da pausa
    "consecutive_losses": 0,        # perdas seguidas
    "size_multiplier": 1.0,         # fator de redução (1.0 = 100%, 0.5 = 50%)
    "peak_capital": settings.INITIAL_CAPITAL,  # pico do capital (p/ drawdown)
    "hard_stopped": False,          # drawdown extremo — só reset manual
    "trailing_highs": {},           # {asset: highest_price} para trailing stop
    "sl_cooldown": {},              # v5.1: {asset: cycles_remaining} — cooldown após stop loss
}
_SL_COOLDOWN_CYCLES = 6  # v5.1: 6 ciclos (~60min) cooldown após stop loss no mesmo ativo


def _update_protection(cycle_pnl: float, capital: float, mom_scores: dict):
    """
    Atualiza o estado de proteção após cada ciclo.
    Lógica:
    - Ganhou? reseta perdas consecutivas, volta tamanho 100%, despausa
    - Perdeu? incrementa consecutivas, reduz tamanho progressivamente
    - Perda diária > 8%? pausa smart (verifica signals antes de voltar)
    - Perda semanal > 15%? opera com 25% do tamanho
    - Drawdown > 40% do pico? HARD STOP (só reset manual)
    """
    state = _protection_state

    # Atualizar pico de capital
    if capital > state["peak_capital"]:
        state["peak_capital"] = capital

    # ── Drawdown absoluto (40% do pico = HARD STOP) ─────────
    drawdown_pct = (state["peak_capital"] - capital) / state["peak_capital"] if state["peak_capital"] > 0 else 0
    if drawdown_pct >= settings.MAX_DRAWDOWN_PERCENTAGE:
        state["hard_stopped"] = True
        state["paused"] = True
        state["pause_reason"] = f"HARD STOP: drawdown {drawdown_pct*100:.1f}% do pico R$ {state['peak_capital']:.2f}"
        msg = f"🔴 {state['pause_reason']} — necessário reset manual"
        _trade_log("HARD_STOP", "—", capital, msg)
        print(f"[proteção] {msg}", flush=True)
        # ── Alerta imediato: Telegram + Discord ──────────────────────────
        if ALERTS_AVAILABLE and alert_manager:
            asyncio.create_task(alert_manager.send_alert(
                "HARD_STOP",
                "🚨 BOT TRAVADO — HARD STOP",
                f"Drawdown de {drawdown_pct*100:.1f}% atingido.\n"
                f"Capital: R${capital:.2f} (pico: R${state['peak_capital']:.2f})\n"
                f"Perda: R${state['peak_capital'] - capital:.2f}\n"
                f"⚠️ Bot parou de operar. Acesse o dashboard para desbloquear.",
            ))
        return

    # ── Ciclo com ganho → recuperação ─────────────────────────
    if cycle_pnl > 0:
        if state["consecutive_losses"] > 0:
            prev_losses = state["consecutive_losses"]
            state["consecutive_losses"] = 0
            state["size_multiplier"] = 1.0
            _trade_log("RECOVERY", "—", capital,
                f"✅ Recuperação! {prev_losses} perdas seguidas → volta 100% tamanho")
        # Se estava pausado, despausa
        if state["paused"] and not state["hard_stopped"]:
            state["paused"] = False
            state["pause_reason"] = ""
            _trade_log("RESUME", "—", capital,
                f"▶️ Bot retomou operação normal — ciclo lucrativo detectado")
        return

    # ── Ciclo com perda → proteção progressiva ────────────────
    if cycle_pnl < 0:
        state["consecutive_losses"] += 1
        n = state["consecutive_losses"]

        # Redução progressiva: 3 perdas → 50%, 5 perdas → 25%, 7+ → 10%
        if n >= 7:
            state["size_multiplier"] = 0.10
        elif n >= 5:
            state["size_multiplier"] = 0.25
        elif n >= settings.CONSECUTIVE_LOSS_REDUCE:
            state["size_multiplier"] = settings.CONSECUTIVE_LOSS_RECOVERY
        else:
            state["size_multiplier"] = 1.0

        if n >= settings.CONSECUTIVE_LOSS_REDUCE:
            _trade_log("LOSS_REDUCE", "—", capital,
                f"⚠️ {n} perdas seguidas → tamanho reduzido para {state['size_multiplier']*100:.0f}%")


def _check_smart_pause(today_pnl: float, week_pnl: float, capital: float, top_scores: dict) -> float:
    """
    Verifica se deve pausar ou retomar. Retorna o multiplier de tamanho (0.0 = pausado total).
    Lógica inteligente: pausa → verifica signals → se bons, volta parcial → se ganha, volta total.
    """
    state = _protection_state

    # Hard stop — nada funciona até reset manual
    if state["hard_stopped"]:
        return 0.0

    max_daily = capital * settings.MAX_DAILY_LOSS_PERCENTAGE
    max_weekly = capital * settings.MAX_WEEKLY_LOSS_PERCENTAGE

    # ── Perda diária excessiva → Smart Pause ─────────────────
    if today_pnl < -max_daily:
        # Verificar se signals melhoraram (momentum forte pode justificar volta)
        best_scores = sorted(top_scores.values(), reverse=True)[:5] if top_scores else []
        avg_top_score = sum(best_scores) / len(best_scores) if best_scores else 0

        if avg_top_score >= settings.RESUME_MOMENTUM_THRESHOLD:
            # Signals fortes! Volta com tamanho reduzido (50%)
            if state["paused"]:
                state["paused"] = False
                state["pause_reason"] = ""
                _trade_log("SMART_RESUME", "—", capital,
                    f"🔄 Momentum forte ({avg_top_score:.2f}) detectado — retomando com 50% do tamanho")
            return 0.50 * state["size_multiplier"]
        else:
            # Signals fracos → mantém pausa
            if not state["paused"]:
                # Primeira vez pausando — envia alerta
                pause_msg = f"Perda diária R$ {today_pnl:.2f} > limite R$ {max_daily:.2f}"
                _trade_log("SMART_PAUSE", "—", capital, f"⏸️ Smart Pause ativado: {pause_msg}")
                if ALERTS_AVAILABLE and alert_manager:
                    asyncio.create_task(alert_manager.send_alert(
                        "SMART_PAUSE",
                        "⏸️ BOT PAUSADO — Perda diária",
                        f"Perda do dia: R${abs(today_pnl):.2f} (limite: R${max_daily:.2f})\n"
                        f"Capital: R${capital:.2f}\n"
                        f"Momentum fraco ({avg_top_score:.2f}) — aguardando recuperação.\n"
                        f"O bot volta automaticamente quando o mercado melhorar.",
                    ))
            state["paused"] = True
            state["pause_reason"] = f"Perda diária R$ {today_pnl:.2f} > limite R$ {max_daily:.2f} (avg momentum: {avg_top_score:.2f})"
            return 0.0

    # ── Perda semanal excessiva → Opera com 25% ──────────────
    if week_pnl < -max_weekly:
        _trade_log("WEEKLY_REDUCE", "—", capital,
            f"📉 Perda semanal R$ {week_pnl:.2f} > limite R$ {max_weekly:.2f} → opera com 25%")
        return 0.25 * state["size_multiplier"]

    # ── Normal → aplica apenas redutor de perdas consecutivas ─
    return state["size_multiplier"]


async def _refresh_signals(klines_5m: dict, top_assets: list):
    """Atualiza cache de sinais avançados (sentimento, orderbook, cross-momentum)."""
    from datetime import timezone as _tzs, timedelta as _tds
    now_str = datetime.now(_tzs(_tds(hours=-3))).strftime("%H:%M")
    # Atualizar a cada 5 minutos
    if _signal_cache.get("ts") == now_str[:4]:
        return
    _signal_cache["ts"] = now_str[:4]
    _signal_cache["cross_mom"] = _cross_asset_momentum(klines_5m)
    # Sentimento e orderbook — limitar a top 5 por performance
    for asset in top_assets[:5]:
        try:
            _signal_cache["sentiment"][asset] = _sentiment_boost(asset)
        except Exception:
            pass
        try:
            _signal_cache["orderbook"][asset] = _orderbook_signal(asset)
        except Exception:
            pass


# ═══════════════════════════════════════════
# ESTRATÉGIAS FASE 2: ATR, GRID, TURBO, VOLUME, PARTIAL TP, MOMENTUM ACCEL
# ═══════════════════════════════════════════

# Cache de ATR por ativo (atualiza a cada ciclo)
_atr_cache: dict = {}

# Cache de momentum anterior para detectar aceleração
_prev_momentum_cache: dict = {}

# Função para calcular pesos dinâmicos por estratégia

def _calculate_dynamic_weights(metrics):
    weights = {}
    for strategy, data in metrics.items():
        pf_weight = min(max(data['profit_factor'] / 2.0, 0.5), 1.5)  # PF influencia entre 0.5 e 1.5
        sharpe_weight = min(max(data['sharpe'] / 2.0, 0.5), 1.5)  # Sharpe influencia entre 0.5 e 1.5
        expectancy_weight = min(max(data['expectancy'] / 0.1, 0.5), 1.5)  # Expectancy recente
        weights[strategy] = pf_weight * 0.4 + sharpe_weight * 0.4 + expectancy_weight * 0.2
    return weights

# Exemplo de uso:
# metrics = {
#     'MR': {'profit_factor': 2.5, 'sharpe': 1.8, 'expectancy': 0.12},
#     'BO': {'profit_factor': 1.8, 'sharpe': 1.2, 'expectancy': 0.08},
# }
# dynamic_weights = _calculate_dynamic_weights(metrics)


def _calculate_atr(prices: list, period: int = None) -> float:
    """
    Calcula Average True Range (ATR) a partir de lista de preços.
    ATR mede volatilidade real — quanto maior, mais volátil o ativo.
    """
    period = period or settings.ATR_PERIOD
    if len(prices) < period + 1:
        return 0.0
    true_ranges = []
    for i in range(1, len(prices)):
        high_low = abs(prices[i] - prices[i-1])  # simplificado (close-to-close)
        true_ranges.append(high_low)
    if len(true_ranges) < period:
        return 0.0
    # ATR = média dos últimos N true ranges
    atr = sum(true_ranges[-period:]) / period
    return atr


def _atr_adaptive_sl_tp(prices: list, asset: str = "") -> tuple:
    """
    Retorna (stop_loss_pct, take_profit_pct) adaptados ao ATR do ativo.
    Ativos voláteis → SL/TP mais largo;  estáveis → mais apertado.
    Isso evita stops prematuros e maximiza ganhos.
    """
    atr = _calculate_atr(prices)
    if atr <= 0 or not prices or prices[-1] <= 0:
        return (settings.STOP_LOSS_PERCENTAGE, settings.TAKE_PROFIT_PERCENTAGE)
    # Normalizar ATR como percentual do preço
    atr_pct = atr / prices[-1]
    # SL = ATR × multiplier, clampado entre min e max
    sl = max(settings.ATR_MIN_SL, min(atr_pct * settings.ATR_SL_MULTIPLIER, settings.ATR_MAX_SL))
    # TP = ATR × multiplier (sempre > SL para manter risk:reward positivo)
    tp = max(sl * 2.0, atr_pct * settings.ATR_TP_MULTIPLIER)  # TP >= 2× SL sempre
    # Cache para logs
    _atr_cache[asset] = {"atr": round(atr, 6), "atr_pct": round(atr_pct, 6), "sl": round(sl, 6), "tp": round(tp, 6)}
    return (sl, tp)


def _grid_trading_pnl(klines: dict, capital_grid: float) -> tuple:
    """
    Grid Trading — lucra em mercado lateral (60% do tempo!).
    Coloca ordens de compra/venda em níveis espaçados.
    Cada oscilação dentro da faixa gera lucro.
    Retorna (pnl, detalhes_dict).
    """
    if not settings.GRID_ENABLED or capital_grid <= 0:
        return (0.0, {})
    pnl = 0.0
    details = {}
    for asset, data in klines.items():
        prices = data.get("prices", [])
        if len(prices) < 10:
            continue
        # Verificar se mercado é lateral (ATR baixo)
        atr = _calculate_atr(prices)
        if atr <= 0 or prices[-1] <= 0:
            continue
        atr_pct = atr / prices[-1]
        # Grid só ativa quando volatilidade está na faixa ideal (lateral)
        if atr_pct > settings.GRID_MIN_RANGE * 2:
            continue  # muito volátil — não é lateral
        if atr_pct < settings.GRID_MIN_RANGE * 0.3:
            continue  # morto — sem movimento
        # Calcular grid levels
        mid_price = prices[-1]
        spacing = mid_price * settings.GRID_SPACING_PCT
        per_level = capital_grid / (settings.GRID_LEVELS * len(klines)) if len(klines) > 0 else 0
        if per_level < 1:
            continue
        # Simular oscilação: preço recente variou entre min e max
        recent = prices[-10:]
        low, high = min(recent), max(recent)
        range_pct = (high - low) / mid_price if mid_price > 0 else 0
        # Cada nível de grid que o preço cruzou gera profit = spacing
        levels_crossed = int(range_pct / settings.GRID_SPACING_PCT)
        levels_crossed = min(levels_crossed, settings.GRID_LEVELS)
        if levels_crossed > 0:
            grid_profit = per_level * settings.GRID_SPACING_PCT * levels_crossed
            pnl += grid_profit
            details[asset] = {"levels_crossed": levels_crossed, "pnl": round(grid_profit, 4)}
    return (round(pnl, 4), details)


def _is_turbo_mode(klines: dict) -> bool:
    """
    Detecta se o mercado está em modo alta-volatilidade (turbo).
    Se sim, o bot deve usar ciclos de 2 minutos em vez de 10.
    """
    if not settings.TURBO_ENABLED:
        return False
    vol_count = 0
    total = 0
    for asset, data in klines.items():
        prices = data.get("prices", [])
        if len(prices) < 5:
            continue
        total += 1
        # Volatilidade = amplitude recente / preço
        recent = prices[-5:]
        vol = (max(recent) - min(recent)) / recent[-1] if recent[-1] > 0 else 0
        if vol > settings.TURBO_VOL_THRESHOLD:
            vol_count += 1
    # Turbo se > 30% dos ativos estão voláteis
    return total > 0 and (vol_count / total) > 0.30


def _volume_confirmed(asset: str, klines: dict) -> float:
    """
    Confirma se o volume suporta a entrada.
    Volume alto → confirma (+boost); Volume baixo → rejeita (penaliza).
    Retorna multiplicador: >1 = confirmado, <1 = fraco, 0 = rejeitar.
    """
    if not settings.VOLUME_CONFIRM_ENABLED:
        return 1.0
    data = klines.get(asset, {})
    volumes = data.get("volumes", [])
    if len(volumes) < 5:
        return 1.0  # sem dados suficientes, neutra
    avg_vol = sum(volumes[-10:]) / len(volumes[-10:]) if len(volumes) >= 10 else sum(volumes) / len(volumes)
    if avg_vol <= 0:
        return 1.0
    current_vol = volumes[-1]
    ratio = current_vol / avg_vol
    if ratio >= settings.VOLUME_CONFIRM_MULTIPLIER:
        return 1.15  # volume forte → +15% confiança
    elif ratio <= settings.VOLUME_REJECT_MULTIPLIER:
        return 0.50  # volume fraco → -50% tamanho (ou skip)
    return 1.0


def _partial_take_profit(ret: float, amt: float) -> tuple:
    """
    Take Profit parcial: realiza 50% no primeiro alvo (0.7%), deixa 50% correr.
    Evita devolver todo o lucro, mas não corta o potencial upside.
    Retorna (pnl_realizado, amt_restante).
    """
    if not settings.PARTIAL_TP_ENABLED:
        return (0.0, amt)
    if ret >= settings.PARTIAL_TP_FIRST_TARGET:
        # Realiza PARTIAL_TP_FIRST_PCT no primeiro alvo
        realized_amt = amt * settings.PARTIAL_TP_FIRST_PCT
        realized_pnl = realized_amt * settings.PARTIAL_TP_FIRST_TARGET
        remaining_amt = amt - realized_amt
        return (realized_pnl, remaining_amt)
    return (0.0, amt)


def _momentum_acceleration(asset: str, current_score: float) -> float:
    """
    Detecta aceleração de momentum — quando a tendência está GANHANDO força.
    Se momentum atual > anterior + threshold → boost de 50%.
    Retorna multiplicador (1.0 = normal, 1.5 = acelerando).
    """
    global _prev_momentum_cache
    if not settings.MOMENTUM_ACCEL_ENABLED:
        return 1.0
    prev_score = _prev_momentum_cache.get(asset, 0)
    _prev_momentum_cache[asset] = current_score
    if prev_score > 0:
        accel = current_score - prev_score
        if accel >= settings.MOMENTUM_ACCEL_THRESHOLD:
            return settings.MOMENTUM_ACCEL_BOOST  # 1.5× — tendência acelerando
        elif accel <= -settings.MOMENTUM_ACCEL_THRESHOLD:
            return 0.7  # momentum desacelerando → reduz 30%
    return 1.0


async def _run_trade_cycle_internal(assets: list = None) -> dict:
    """Lógica interna de um ciclo de trading com alocação em 3 timeframes (10/25/65%)."""
    capital = _trade_state["capital"]
    all_assets = assets if assets is not None else settings.ALL_ASSETS
    b3_open = _is_market_open()
    session_label = "B3+Crypto" if (assets is None or len(all_assets) > len(settings.CRYPTO_ASSETS)) else "Crypto24/7"
    data_source = "test"

    # ── 1. Buscar dados para os 3 timeframes ─────────────────────────────
    klines_by_tf: dict = {"5m": None, "1h": None, "1d": None}
    # 100 candles: mínimo para ADX (42), Hurst (20) e ATR (20) funcionarem corretamente
    _TF_CANDLES = {"5m": 100, "1h": 100, "1d": 100}
    if MARKET_DATA_AVAILABLE and market_data_service:
        for tf in ("5m", "1h", "1d"):
            try:
                k = await market_data_service.get_all_klines(all_assets, tf, _TF_CANDLES[tf])
                if k:
                    klines_by_tf[tf] = k
                    data_source = "brapi/yahoo"
            except Exception:
                pass
    # Fallback para dados de teste
    for tf in ("5m", "1h", "1d"):
        if not klines_by_tf[tf]:
            klines_by_tf[tf] = test_assets_data

    # ── 2. Top N ativos por momentum (com filtro de score mínimo) ────────
    min_score = settings.MIN_MOMENTUM_SCORE
    no_position_reason = ""

    def _top_assets(klines, n):
        mom = MomentumAnalyzer.calculate_multiple_assets(klines)
        # Filtra score mínimo — só opera quando vale a pena
        filtered = {a: m for a, m in mom.items()
                    if m.get("momentum_score", 0) >= min_score}
        sorted_assets = sorted(filtered.items(),
                               key=lambda x: x[1].get("momentum_score", 0), reverse=True)
        return [a for a, _ in sorted_assets[:n]], mom

    top_5m, mom_5m = _top_assets(klines_by_tf["5m"], _TIMEFRAME_N_ASSETS["5m"])
    top_1h, mom_1h = _top_assets(klines_by_tf["1h"], _TIMEFRAME_N_ASSETS["1h"])
    top_1d, mom_1d = _top_assets(klines_by_tf["1d"], _TIMEFRAME_N_ASSETS["1d"])

    if not top_5m and not top_1h and not top_1d:
        no_position_reason = f"Sem sinal válido: nenhum ativo acima do momentum mínimo ({min_score:.2f})."

    # ── 2b. Atualizar sinais avançados (sentimento, orderbook, cross-momentum)
    try:
        all_top = list(set(top_5m + top_1h + top_1d))
        await _refresh_signals(klines_by_tf["5m"], all_top)
    except Exception:
        pass

    # ── 3. Risco (referência via 5m) ─────────────────────────────────────
    ref_asset = top_5m[0] if top_5m else list(klines_by_tf["5m"].keys())[0]
    ref_data  = klines_by_tf["5m"].get(ref_asset, {})
    risk_analysis = RiskAnalyzer.calculate_irq(ref_data.get("prices", []), ref_data.get("volumes", []))
    irq_score  = risk_analysis["irq_score"]
    protection = RiskAnalyzer.get_protection_level(irq_score)

    effective_alloc, strategy_regime, strategy_reason, strategy_tf_recent = _effective_timeframe_alloc(irq_score)
    capital_5m = round(capital * effective_alloc["5m"], 2)
    capital_1h = round(capital * effective_alloc["1h"], 2)
    capital_1d = round(capital * effective_alloc["1d"], 2)

    # ── Regime Detection v3.0 — ADX + ATR Ratio + Hurst Exponent ─────────
    # Substitui ADX simples por detector multi-sinal com 3 indicadores
    _regime_result = RegimeDetector.detect(ref_data.get("prices", []))
    _adx_val    = _regime_result["adx"]
    _adx_regime = _regime_result["regime"]   # TREND_STRONG/TREND_WEAK/LATERAL/HIGH_VOL/LOW_VOL_SQUEEZE/NEUTRAL
    _atr_ratio  = _regime_result["atr_ratio"]
    _hurst      = _regime_result["hurst"]
    _regime_conf = _regime_result["confidence"]
    _trend_dir  = _regime_result.get("direction", "neutral")  # up/down/neutral

    # ── v5.0 Nível 1 #1: Desativar 5m em regime sem tendência (ADX < 25) ──
    # 5m gerava prejuízo (-R$0.47) → só opera quando há tendência confirmada
    if _adx_val < 25.0 and _adx_regime in ("NEUTRAL", "LATERAL", "LOW_VOL_SQUEEZE"):
        capital_5m = 0.0
        top_5m = []
        _trade_log("5M_DISABLED", "—", 0,
            f"⚠️ 5m desativado: regime={_adx_regime} ADX={_adx_val:.1f} < 25 — sem tendência para scalping")

    # ── v5.0 Nível 2 #6: Auto regime switch (ADX < 20 → lateral mode) ──
    # Quando mercado lateral: desativa momentum (5m/1h), boost VR (+80%) e MR (+50%)
    _regime_switch_active = False
    if _adx_val < 20.0 and _adx_regime in ("LATERAL", "NEUTRAL", "LOW_VOL_SQUEEZE"):
        _regime_switch_active = True
        capital_5m = 0.0
        top_5m = []
        capital_1h = round(capital_1h * 0.25, 2)  # v5.1: reduz 1h em 75% (era 50%) — 1h negativo em NEUTRAL
        # O capital liberado será redirecionado via boost nos buckets MR e VR
        _trade_log("REGIME_SWITCH", "—", 0,
            f"🔄 Auto Regime Switch LATERAL: ADX={_adx_val:.1f} < 20 — "
            f"5m=OFF, 1h×0.25, VR boost ×1.8, MR boost ×1.5")

    # Log regime + direction para visibilidade
    if _trend_dir == "down":
        _trade_log("REGIME_DOWNTREND", "—", 0,
            f"⬇️ DOWNTREND detectado: regime={_adx_regime} dir={_trend_dir} ADX={_adx_val:.1f} "
            f"Hurst={_hurst:.3f} conf={_regime_conf:.2f} — exposição reduzida 75%")

    # ── 3b. Dynamic Kelly Criterion v5.0 + sinais avançados ──────────
    # Usa win_rate REAL do histórico em vez de estimar pelo score
    _hist_wins   = int(_perf_state.get("win_count", 0) or 0)
    _hist_losses = int(_perf_state.get("loss_count", 0) or 0)
    _hist_total  = _hist_wins + _hist_losses
    _hist_gain   = float(_perf_state.get("total_gain", 0.0) or 0.0)
    _hist_loss_a = float(_perf_state.get("total_loss", 0.0) or 0.0)  # already positive

    if _hist_total >= 15:
        # Dados suficientes → usar win rate real
        _real_wr = _hist_wins / _hist_total
        # Reward/Risk real = avg_gain / avg_loss
        _avg_gain = (_hist_gain / _hist_wins) if _hist_wins > 0 else 0.01
        _avg_loss = (_hist_loss_a / _hist_losses) if _hist_losses > 0 else 0.01
        _real_rr  = _avg_gain / _avg_loss if _avg_loss > 0 else 1.5
    else:
        # Poucos trades → usar defaults conservadores
        _real_wr = 0.55
        _real_rr = 1.3

    # Clamp para segurança
    _real_wr = max(0.35, min(0.80, _real_wr))
    _real_rr = max(0.5, min(3.0, _real_rr))

    _trade_log("KELLY_DYNAMIC", "—", 0,
        f"📊 Kelly Dinâmico: WR={_real_wr:.1%} RR={_real_rr:.2f} (trades={_hist_total}, wins={_hist_wins})")

    def _kelly_weight(score: float, base_amount: float, asset: str = "") -> float:
        """Retorna alocação ajustada via Kelly fracionário dinâmico + sinais avançados.
        v5.0: usa win_rate e R:R reais do histórico."""
        if score <= 0:
            return base_amount
        # Kelly dinâmico: f* = (p*b - q) / b com win_rate e RR reais
        # Score modula o Kelly: score alto → confiança maior → Kelly mais agressivo
        score_adj = min(score, 0.85)
        p = _real_wr * (0.7 + 0.3 * score_adj)  # score modula ±30% do WR
        p = max(0.10, min(0.85, p))
        q = 1.0 - p
        b = _real_rr
        kelly_f = (p * b - q) / b if b > 0 else 0
        kelly_f = max(0.05, min(kelly_f, 0.60))  # clamp 5%-60%
        adjusted = base_amount * (1.0 + kelly_f * settings.KELLY_FRACTION)
        # Aplicar boosts de sinais avançados (sentiment, order book, cross-momentum)
        if asset:
            sentiment = _signal_cache.get("sentiment", {}).get(asset, 1.0)
            orderbook = _signal_cache.get("orderbook", {}).get(asset, 1.0)
            cross_mom = _signal_cache.get("cross_mom", {}).get(asset, 1.0)
            adjusted = adjusted * sentiment * orderbook * cross_mom
        return round(adjusted, 2)

    # ── 3c. Mean Reversion v2.1 — usa engine com BB + RSI + divergência ─
    # (substituiu a função naïve que só checava queda >3%, pegava faca caindo)

    # ── 3d. DCA inteligente — acumula em posições perdedoras ─────────────
    def _dca_adjust(asset: str, current_amount: float, klines: dict) -> float:
        """Se ativo já está em posição e caiu, aumenta posição (DCA)."""
        prev_pos = _trade_state.get("positions", {}).get(asset)
        if not prev_pos:
            return current_amount
        prev_change = prev_pos.get("change_pct", 0)
        # Se caiu entre -1% e -3%, aloca 50% a mais (DCA)
        if -3.0 < prev_change < -1.0:
            return round(current_amount * 1.5, 2)
        return current_amount

    # ── Mean Reversion: bucket dedicado (usa 1h para sinais mais confiáveis) ─
    # v2.1: não adiciona mais ao top_5m — tem capital próprio
    mr_results  = MeanReversionAnalyzer.calculate_multiple_assets(klines_by_tf["1h"], top_n=2)
    top_mr      = list(mr_results.keys())
    # Adapta mr_results para o formato que _calc_pnl_bucket espera (momentum_score >= 0.55)
    mom_mr      = {
        a: {**d, "momentum_score": max(d["mr_score"], 0.56)}
        for a, d in mr_results.items()
    }
    capital_mr  = round(capital * _MR_ALLOC_PCT, 2) if top_mr else 0.0

    # ── Breakout: bucket dedicado de 8% (usa 1h para sinais intraday) ────
    # v2.2: detecta rompimentos de resistência/suporte com confirmação de volume
    bo_results  = BreakoutAnalyzer.calculate_multiple_assets(klines_by_tf["1h"], top_n=1)
    top_bo      = list(bo_results.keys())
    # Adapta bo_results para o formato que _calc_pnl_bucket espera (momentum_score >= 0.55)
    mom_bo      = {
        a: {**d, "momentum_score": max(d["breakout_score"], 0.61)}
        for a, d in bo_results.items()
    }
    capital_bo  = round(capital * _BO_ALLOC_PCT, 2) if top_bo else 0.0

    # ── Squeeze: bucket dedicado de 6% (Volatility Compression → Expansion) ─
    # v2.3: entra no início da expansão após Bollinger Squeeze confirmado por Keltner
    sq_results  = SqueezeAnalyzer.calculate_multiple_assets(klines_by_tf["1h"], top_n=1)
    sq_results  = {a: d for a, d in sq_results.items() if d.get("direction") == "LONG"}  # long-only
    top_sq      = list(sq_results.keys())
    mom_sq      = {
        a: {**d, "momentum_score": max(d["squeeze_score"], 0.59)}
        for a, d in sq_results.items()
    }
    capital_sq  = round(capital * _SQ_ALLOC_PCT, 2) if top_sq else 0.0

    # ── Liquidity Sweep: bucket de 2% (Stop Hunt / Caça de Liquidez) ────
    # v3.0: detecta falsas rupturas seguidas de reversão institucional
    ls_results  = LiquiditySweepAnalyzer.calculate_multiple_assets(klines_by_tf["1h"], top_n=1)
    ls_results  = {a: d for a, d in ls_results.items() if d.get("direction") == "LONG"}  # long-only
    top_ls      = list(ls_results.keys())
    mom_ls      = {
        a: {**d, "momentum_score": max(d["sweep_score"], 0.61)}
        for a, d in ls_results.items()
    }
    capital_ls  = round(capital * _LS_ALLOC_PCT, 2) if top_ls else 0.0

    # ── v5.0 Nível 1 #3: Threshold mínimo LS R$25 ──────────────────
    # Com capital baixo, LS com R$9 gera custos > lucro → desativar se < R$25
    if capital_ls < 25.0:
        capital_ls = 0.0
        top_ls = []

    # ── Fair Value Gap: bucket de 2% (Imbalance Fill) ───────────────────
    # v3.0: detecta gaps institucionais e opera o preenchimento
    fvg_results = FVGAnalyzer.calculate_multiple_assets(klines_by_tf["1h"], top_n=1)
    fvg_results = {a: d for a, d in fvg_results.items() if d.get("direction") == "LONG"}  # long-only
    top_fvg     = list(fvg_results.keys())
    mom_fvg     = {
        a: {**d, "momentum_score": max(d["fvg_score"], 0.61)}
        for a, d in fvg_results.items()
    }
    capital_fvg = round(capital * _FVG_ALLOC_PCT, 2) if top_fvg else 0.0

    # ── VWAP Reversion: bucket de 10% (Reversão ao VWAP) ────────────────
    # v4.0: entra quando preço desvia >1.5σ do VWAP em mercado lateral (ADX<20)
    # Score: desvio VWAP 40% + RSI 35% + Volume 25%. SL=0.8 ATR, TP=1.0 ATR
    vr_results  = VWAPReversionAnalyzer.calculate_multiple_assets(klines_by_tf["1h"], top_n=2)
    vr_results  = {a: d for a, d in vr_results.items() if d.get("direction") == "LONG"}  # long-only: SHORT=overbought, não comprar
    top_vr      = list(vr_results.keys())
    mom_vr      = {
        a: {**d, "momentum_score": max(d["vr_score"], 0.55)}
        for a, d in vr_results.items()
    }
    capital_vr  = round(capital * _VR_ALLOC_PCT, 2) if top_vr else 0.0

    # ── Pyramid Breakout: bucket de 9% (Breakout com Pirâmide Progressiva) ─
    # v4.0: detecta rompimentos fortes (BB + ATR expansion + EMA alignment)
    # Pyramiding: L1=100%, +1ATR→+50%, +2ATR→+30% (max 180%). SL=1.5 ATR, TP=3.0 ATR
    pb_results  = PyramidBreakoutAnalyzer.calculate_multiple_assets(klines_by_tf["1h"], top_n=2)
    pb_results  = {a: d for a, d in pb_results.items() if d.get("direction") == "LONG"}  # long-only
    top_pb      = list(pb_results.keys())
    # Aplica multiplicador de pirâmide ao momentum_score para boost de posição
    mom_pb      = {}
    for a, d in pb_results.items():
        _pyr_mult = d.get("pyramid_multiplier", 1.0)
        _pb_score = max(d["pb_score"], 0.58)
        # Boost: score ajustado pelo multiplicador de pirâmide (cap 1.0)
        _adj_score = min(_pb_score * (1.0 + (_pyr_mult - 1.0) * 0.3), 1.0)
        mom_pb[a] = {**d, "momentum_score": _adj_score}
    capital_pb  = round(capital * _PB_ALLOC_PCT, 2) if top_pb else 0.0

    # ── Regime Routing v4.0 — aplica multipliers do RegimeDetector ───────
    # Usa ADX + ATR Ratio + Hurst para rotear capital de forma ótima
    _base_caps = {"mr": capital_mr, "bo": capital_bo, "sq": capital_sq,
                  "ls": capital_ls, "fvg": capital_fvg,
                  "vr": capital_vr, "pb": capital_pb}
    _regime_caps = RegimeDetector.apply_multipliers(_base_caps, _regime_result)
    capital_mr  = round(_regime_caps["mr"], 2)
    capital_bo  = round(_regime_caps["bo"], 2)
    capital_sq  = round(_regime_caps["sq"], 2)
    capital_ls  = round(_regime_caps["ls"], 2)
    capital_fvg = round(_regime_caps["fvg"], 2)
    capital_vr  = round(_regime_caps["vr"], 2)
    capital_pb  = round(_regime_caps["pb"], 2)

    # ── Dynamic Strategy Allocation — ajuste fino por Sharpe histórico ───
    _dyn_weights = _dynamic_strategy_weights(lookback=30)
    capital_mr  = round(capital_mr  * _dyn_weights.get("mr",  1.0), 2)
    capital_bo  = round(capital_bo  * _dyn_weights.get("bo",  1.0), 2)
    capital_sq  = round(capital_sq  * _dyn_weights.get("sq",  1.0), 2)
    capital_ls  = round(capital_ls  * _dyn_weights.get("ls",  1.0), 2)
    capital_fvg = round(capital_fvg * _dyn_weights.get("fvg", 1.0), 2)
    capital_vr  = round(capital_vr  * _dyn_weights.get("vr",  1.0), 2)
    capital_pb  = round(capital_pb  * _dyn_weights.get("pb",  1.0), 2)

    # ── Exposure Cap — nenhum bucket ultrapassa 2× sua alocação base ─────
    # Previne que regime + dynamic weights se acumulem e criem alavancagem excessiva
    # Exemplo: regime TREND_STRONG (1.55×) + dyn_weight 1.50× = 2.32× sem cap
    capital_mr  = min(capital_mr,  round(capital * _MR_ALLOC_PCT  * 2.0, 2))
    capital_bo  = min(capital_bo,  round(capital * _BO_ALLOC_PCT  * 2.0, 2))
    capital_sq  = min(capital_sq,  round(capital * _SQ_ALLOC_PCT  * 2.0, 2))
    capital_ls  = min(capital_ls,  round(capital * _LS_ALLOC_PCT  * 2.0, 2))
    capital_fvg = min(capital_fvg, round(capital * _FVG_ALLOC_PCT * 2.0, 2))
    capital_vr  = min(capital_vr,  round(capital * _VR_ALLOC_PCT  * 2.0, 2))
    capital_pb  = min(capital_pb,  round(capital * _PB_ALLOC_PCT  * 2.0, 2))

    # ── v5.0 Nível 2 #6: Aplicar boost VR/MR quando regime switch lateral ──
    if _regime_switch_active:
        capital_vr  = round(capital_vr * 1.80, 2)   # VR excela em lateral → +80%
        capital_mr  = round(capital_mr * 1.50, 2)    # MR também lucra em lateral → +50%
        capital_bo  = round(capital_bo * 0.30, 2)    # Breakout falha em lateral → -70%
        capital_pb  = round(capital_pb * 0.20, 2)    # Pyramid Breakout péssimo → -80%
        # Re-cap para não estourar (3× base em regime switch é aceitável)
        capital_vr  = min(capital_vr, round(capital * _VR_ALLOC_PCT * 3.0, 2))
        capital_mr  = min(capital_mr, round(capital * _MR_ALLOC_PCT * 3.0, 2))

    cycle_costs = {
        "total": 0.0,
        "brokerage": 0.0,
        "exchange_fees": 0.0,
        "spread": 0.0,
        "slippage": 0.0,
        "fx": 0.0,
        "min_fee_adj": 0.0,
        "by_market": {},
    }

    # ── 4. P&L por bucket (ATR SL/TP + Volume + Partial TP + Momentum Accel + Kelly + DCA + custos) ──
    def _calc_pnl_bucket(top_list, klines, bucket_capital, tf_name: str, mom_data=None):
        if not top_list:
            return 0.0, {}, {"total": 0.0}
        per_asset = round(bucket_capital / len(top_list), 2)
        pnl = 0.0
        positions = {}
        bucket_costs = {
            "total": 0.0,
            "brokerage": 0.0,
            "exchange_fees": 0.0,
            "spread": 0.0,
            "slippage": 0.0,
            "fx": 0.0,
            "min_fee_adj": 0.0,
        }
        for asset in top_list:
            prices = klines.get(asset, {}).get("prices", []) if klines else []
            if len(prices) >= 2 and prices[-2] != 0:
                ret = (prices[-1] - prices[-2]) / prices[-2]
            else:
                ret = 0.0

            # ── Realismo paper mode: adverse selection + falha de execução ──
            # Parâmetros controlados pelo _sim_mode (normal/stress/extreme)
            _sp = _SIM_PROFILES.get(_sim_mode, _SIM_PROFILES["normal"])
            if _rnd.random() < _sp["rejection_rate"]:
                continue
            # Filtro de retorno mínimo: skip se |ret| menor que custo estimado
            if abs(ret) < _sp["min_ret_threshold"]:
                continue
            # Adverse selection: entra depois do sinal → captura % do movimento
            if ret > 0:
                capture = max(0.0, _rnd.gauss(_sp["capture_mean"], _sp["capture_std"]))
                ret = ret * min(capture, 1.05)   # máx 105% (overshoot ocasional)
            else:
                # Na queda: adverse selection piora a perda
                worsen = max(1.0, _rnd.gauss(_sp["loss_worsen_mean"], _sp["loss_worsen_std"]))
                ret = ret * worsen
            ret += _rnd.gauss(_sp["noise_mean"], _sp["noise_std"])  # ruído de execução

            # ── ATR Adaptive SL/TP — calcula limites dinâmicos por ativo ──
            atr_sl, atr_tp = _atr_adaptive_sl_tp(prices, asset)
            if _strategy_state.get("tf_risk_tuning_enabled", True):
                sl_mult = _strategy_state.get("tf_sl_mult", {}).get(tf_name, 1.0)
                tp_mult = _strategy_state.get("tf_tp_mult", {}).get(tf_name, 1.0)
                atr_sl = max(0.002, min(atr_sl * sl_mult, 0.08))
                atr_tp = max(0.003, min(atr_tp * tp_mult, 0.12))

            # ── v5.1: Stop-Loss Cooldown — evita re-entrada após stop recente ──
            _sl_cd = _protection_state.get("sl_cooldown", {}).get(asset, 0)
            if _sl_cd > 0:
                continue  # ativo em cooldown após stop loss

            # Kelly + sinais avançados: ajustar tamanho pelo score + sentiment/orderbook
            score = 0.5
            if mom_data and asset in mom_data:
                score = mom_data[asset].get("momentum_score", 0.5)
            # ── Filtro de qualidade: só entrar quando score >= 0.58 ──
            # v3 (2026-03-09): elevado de 0.55→0.58 para melhorar WR e PF
            if score < 0.58:
                continue

            # ── v5.1 Filtro RSI no 1h — evita comprar overbought ──
            # 1h está negativo recente → RSI de 55→50 para ser mais seletivo
            if tf_name == "1h" and mom_data and asset in mom_data:
                _asset_rsi = mom_data[asset].get("rsi", 50.0)
                if _asset_rsi > 50.0 and ret > 0:
                    # RSI overbought + sinal de compra → skip (provavelmente topo)
                    continue

            amt = _kelly_weight(score, per_asset, asset)
            # DCA: aumentar se já estava em posição perdedora
            amt = _dca_adjust(asset, amt, klines)

            # ── Volume Confirmation — filtra entradas com volume fraco ────
            vol_mult = _volume_confirmed(asset, klines)
            amt = round(amt * vol_mult, 2)

            # ── Momentum Acceleration — boost quando tendência acelera ────
            mom_accel_mult = _momentum_acceleration(asset, score)
            amt = round(amt * mom_accel_mult, 2)

            # Aplicar multiplicador de proteção (perdas consecutivas, pausa parcial)
            amt = round(amt * _protection_state["size_multiplier"], 2)
            # Clamp: não ultrapassa 30% do capital total
            amt = min(amt, capital * settings.MAX_POSITION_PERCENTAGE)

            # ── Trailing Stop: protege lucro parcial ─────────────
            if prices and len(prices) >= 2:
                current_price = prices[-1]
                prev_high = _protection_state["trailing_highs"].get(asset, current_price)
                # Atualiza pico
                if current_price > prev_high:
                    _protection_state["trailing_highs"][asset] = current_price
                    prev_high = current_price
                # Se caiu X% do pico → trailing stop ativado
                if prev_high > 0:
                    drop_from_peak = (prev_high - current_price) / prev_high
                    if drop_from_peak >= settings.TRAILING_STOP_PERCENTAGE and ret > 0:
                        # Tinha lucro mas devolveu — trava no trailing (retém 60% do lucro)
                        ret = max(ret * 0.60, 0.002)  # retém 60% do lucro + mínimo R:R boost
                        _trade_log("TRAILING_STOP", asset, amt,
                            f"📊 Trailing Stop {asset}: pico R$ {prev_high:.4f} → atual {current_price:.4f} (-{drop_from_peak*100:.2f}%)")
                    # Breakeven stop: se já teve +1.0% e devolveu, garante pelo menos +0.3%
                    elif ret > 0.010 and drop_from_peak > 0.002:
                        ret = max(ret, 0.003)  # garante breakeven + 0.3% (era 0.2%)
            else:
                # Sem dados suficientes, limpa trailing
                _protection_state["trailing_highs"].pop(asset, None)

            # ── SL fixo -2% para swing 1d — proteção mínima overnight ────
            if tf_name == "1d" and ret <= -0.02:
                ret = -0.02
                _trade_log("STOP_LOSS_1D", asset, amt,
                    f"🛑 SL Swing 1d {asset}: -2% atingido — saindo posição swing")
                _protection_state["trailing_highs"].pop(asset, None)
                _protection_state["sl_cooldown"][asset] = _SL_COOLDOWN_CYCLES

            # ── ATR Stop Loss (adaptativo) — substitui SL fixo ──────────
            if ret <= -atr_sl:
                ret = -atr_sl
                _trade_log("STOP_LOSS_ATR", asset, amt,
                    f"🛑 ATR Stop Loss {asset}: {ret*100:.2f}% (ATR SL={atr_sl*100:.2f}%)")
                _protection_state["trailing_highs"].pop(asset, None)
                # v5.1: Cooldown — não re-entrar neste ativo por N ciclos
                _protection_state["sl_cooldown"][asset] = _SL_COOLDOWN_CYCLES

            # ── Partial Take Profit — realiza 50% no primeiro alvo ───────
            partial_pnl = 0.0
            if ret >= settings.PARTIAL_TP_FIRST_TARGET and settings.PARTIAL_TP_ENABLED:
                partial_pnl, amt_remaining = _partial_take_profit(ret, amt)
                if partial_pnl > 0:
                    _trade_log("PARTIAL_TP", asset, amt,
                        f"💰 Partial TP {asset}: +{settings.PARTIAL_TP_FIRST_TARGET*100:.1f}% em {settings.PARTIAL_TP_FIRST_PCT*100:.0f}% da posição = R$ {partial_pnl:.4f}")
                    amt = amt_remaining  # resto continua correndo

            # ── ATR Take Profit (adaptativo) — substitui TP fixo ─────────
            if ret >= atr_tp:
                ret = atr_tp
                _trade_log("TAKE_PROFIT_ATR", asset, amt,
                    f"💰 ATR Take Profit {asset}: +{ret*100:.2f}% (ATR TP={atr_tp*100:.2f}%)")
                _protection_state["trailing_highs"].pop(asset, None)

            gross_pnl = (amt * ret) + partial_pnl
            costs = _estimate_trade_costs_brl(asset, amt, abs(ret))
            net_pnl = gross_pnl - costs["total"]

            pnl += net_pnl
            for k in ("total", "brokerage", "exchange_fees", "spread", "slippage", "fx", "min_fee_adj"):
                bucket_costs[k] = round(bucket_costs.get(k, 0.0) + costs.get(k, 0.0), 6)
                cycle_costs[k] = round(cycle_costs.get(k, 0.0) + costs.get(k, 0.0), 6)
            mk = costs.get("market", "other")
            cycle_costs["by_market"][mk] = round(cycle_costs["by_market"].get(mk, 0.0) + costs.get("total", 0.0), 6)

            positions[asset] = {"amount": amt, "ret_pct": round(ret * 100, 3),
                                "atr_sl": round(atr_sl * 100, 2), "atr_tp": round(atr_tp * 100, 2),
                                "vol_mult": round(vol_mult, 2), "mom_accel": round(mom_accel_mult, 2),
                                "market": mk, "gross_pnl": round(gross_pnl, 4),
                                "costs": round(costs.get("total", 0.0), 4), "net_pnl": round(net_pnl, 4)}
        return round(pnl, 4), positions, bucket_costs

    pnl_5m, pos_5m, costs_5m = _calc_pnl_bucket(top_5m, klines_by_tf["5m"], capital_5m, "5m", mom_5m)
    pnl_1h, pos_1h, costs_1h = _calc_pnl_bucket(top_1h, klines_by_tf["1h"], capital_1h, "1h", mom_1h)
    pnl_1d, pos_1d, costs_1d = _calc_pnl_bucket(top_1d, klines_by_tf["1d"], capital_1d, "1d", mom_1d)

    # ── 4a-mr. Mean Reversion bucket — bucket dedicado de 10% ────────────
    pnl_mr, pos_mr, costs_mr = _calc_pnl_bucket(top_mr, klines_by_tf["1h"], capital_mr, "mr", mom_mr)
    if top_mr:
        _trade_log("MR_SIGNAL", "—", capital_mr,
            f"🔁 Mean Reversion: {len(top_mr)} ativo(s) | {list(top_mr)} | R${capital_mr:.2f} | P&L: R${pnl_mr:+.4f}")

    # ── 4a-bo. Breakout bucket — bucket dedicado de 8% ───────────────────
    pnl_bo, pos_bo, costs_bo = _calc_pnl_bucket(top_bo, klines_by_tf["1h"], capital_bo, "bo", mom_bo)
    if top_bo:
        _trade_log("BO_SIGNAL", "—", capital_bo,
            f"🚀 Breakout: {len(top_bo)} ativo(s) | {list(top_bo)} | R${capital_bo:.2f} | P&L: R${pnl_bo:+.4f}")

    # ── 4a-sq. Squeeze bucket — Volatility Compression → Expansion (6%) ──
    pnl_sq, pos_sq, costs_sq = _calc_pnl_bucket(top_sq, klines_by_tf["1h"], capital_sq, "sq", mom_sq)
    if top_sq:
        _sq_dir  = sq_results.get(list(top_sq)[0], {}).get("direction", "?")
        _sq_bars = sq_results.get(list(top_sq)[0], {}).get("squeeze_bars", 0)
        _trade_log("SQ_SIGNAL", "—", capital_sq,
            f"🎯 Squeeze {_sq_dir}: {len(top_sq)} ativo(s) | {list(top_sq)} | {_sq_bars} bars | R${capital_sq:.2f} | P&L: R${pnl_sq:+.4f} | Regime={_adx_regime}")

    # ── 4a-ls. Liquidity Sweep bucket — Stop Hunt / Reversão Institucional (2%) ──
    pnl_ls, pos_ls, costs_ls = _calc_pnl_bucket(top_ls, klines_by_tf["1h"], capital_ls, "ls", mom_ls)
    if top_ls:
        _ls_dir   = ls_results.get(list(top_ls)[0], {}).get("direction", "?")
        _ls_depth = ls_results.get(list(top_ls)[0], {}).get("sweep_depth_pct", 0.0)
        _trade_log("LS_SIGNAL", "—", capital_ls,
            f"🎣 LiqSweep {_ls_dir}: {len(top_ls)} ativo(s) | {list(top_ls)} | depth={_ls_depth*100:.2f}% | R${capital_ls:.2f} | P&L: R${pnl_ls:+.4f}")

    # ── 4a-fvg. Fair Value Gap bucket — Imbalance Fill (2%) ─────────────
    pnl_fvg, pos_fvg, costs_fvg = _calc_pnl_bucket(top_fvg, klines_by_tf["1h"], capital_fvg, "fvg", mom_fvg)
    if top_fvg:
        _fvg_dir  = fvg_results.get(list(top_fvg)[0], {}).get("direction", "?")
        _fvg_gap  = fvg_results.get(list(top_fvg)[0], {}).get("gap_size_pct", 0.0)
        _trade_log("FVG_SIGNAL", "—", capital_fvg,
            f"📐 FVG {_fvg_dir}: {len(top_fvg)} ativo(s) | {list(top_fvg)} | gap={_fvg_gap:.2f}% | R${capital_fvg:.2f} | P&L: R${pnl_fvg:+.4f}")

    # ── 4a-vr. VWAP Reversion bucket — Reversão ao VWAP (10%) ───────────
    pnl_vr, pos_vr, costs_vr = _calc_pnl_bucket(top_vr, klines_by_tf["1h"], capital_vr, "vr", mom_vr)
    if top_vr:
        _vr_dir   = vr_results.get(list(top_vr)[0], {}).get("direction", "?")
        _vr_dev   = vr_results.get(list(top_vr)[0], {}).get("vwap_deviation", 0.0)
        _trade_log("VR_SIGNAL", "—", capital_vr,
            f"📊 VWAP Rev {_vr_dir}: {len(top_vr)} ativo(s) | {list(top_vr)} | desvio={_vr_dev*100:.2f}% | R${capital_vr:.2f} | P&L: R${pnl_vr:+.4f} | Regime={_adx_regime}")

    # ── 4a-pb. Pyramid Breakout bucket — Breakout Progressivo (9%) ──────
    pnl_pb, pos_pb, costs_pb = _calc_pnl_bucket(top_pb, klines_by_tf["1h"], capital_pb, "pb", mom_pb)
    if top_pb:
        _pb_dir   = pb_results.get(list(top_pb)[0], {}).get("direction", "?")
        _pb_pyr   = pb_results.get(list(top_pb)[0], {}).get("pyramid_multiplier", 1.0)
        _pb_level = pb_results.get(list(top_pb)[0], {}).get("pyramid_level", 1)
        _trade_log("PB_SIGNAL", "—", capital_pb,
            f"🔺 PyramidBO {_pb_dir}: {len(top_pb)} ativo(s) | {list(top_pb)} | pyramid=L{_pb_level} ({_pb_pyr:.0%}) | R${capital_pb:.2f} | P&L: R${pnl_pb:+.4f} | Regime={_adx_regime}")

    # ── 4b. Grid Trading — lucra com mercado lateral ─────────────────────
    grid_capital = round(capital * settings.GRID_CAPITAL_PCT, 2)
    grid_pnl, grid_details = _grid_trading_pnl(klines_by_tf["5m"], grid_capital)
    if grid_pnl > 0:
        _trade_log("GRID_PROFIT", "—", grid_pnl,
            f"📊 Grid Trading: +R$ {grid_pnl:.4f} | {len(grid_details)} ativos em grid | Capital grid: R$ {grid_capital:.2f}")

    fees_total = round(cycle_costs.get("total", 0.0), 4)
    gross_cycle_pnl = round(pnl_5m + pnl_1h + pnl_1d + pnl_mr + pnl_bo + pnl_sq + pnl_ls + pnl_fvg + pnl_vr + pnl_pb + grid_pnl + fees_total, 4)
    cycle_pnl = round(pnl_5m + pnl_1h + pnl_1d + pnl_mr + pnl_bo + pnl_sq + pnl_ls + pnl_fvg + pnl_vr + pnl_pb + grid_pnl, 4)

    # ── 4b. Proteção Inteligente (Smart Pause/Resume + Drawdown + Semanal) ─
    from datetime import timezone as _tz2, timedelta as _td2
    _brt2 = _tz2(_td2(hours=-3))
    _now_brt2 = datetime.now(_brt2)
    _today2 = _now_brt2.strftime("%Y-%m-%d")

    # Calcular PnL diário
    _today_cycles = [c for c in _perf_state.get("cycles", []) if c.get("timestamp", "").startswith(_today2)]
    _today_pnl = sum(c.get("pnl", 0) for c in _today_cycles) + cycle_pnl

    # Calcular PnL semanal (últimos 7 dias)
    _week_ago = (_now_brt2 - _td2(days=7)).strftime("%Y-%m-%d")
    _week_cycles = [c for c in _perf_state.get("cycles", []) if c.get("timestamp", "") >= _week_ago]
    _week_pnl = sum(c.get("pnl", 0) for c in _week_cycles) + cycle_pnl

    # Coletar scores de momentum para decisão de resume
    _all_mom_scores = {}
    for m in (mom_5m, mom_1h, mom_1d, mom_mr, mom_bo, mom_sq, mom_ls, mom_fvg, mom_vr, mom_pb):
        for a, d in m.items():
            s = d.get("momentum_score", 0)
            if a not in _all_mom_scores or s > _all_mom_scores[a]:
                _all_mom_scores[a] = s

    # Atualizar estado de proteção (consecutivas, drawdown, etc.)
    _update_protection(cycle_pnl, capital, _all_mom_scores)

    # v5.1: Decrementar cooldown de stop-loss por ativo
    _sl_cd = _protection_state.get("sl_cooldown", {})
    _expired = [a for a, c in _sl_cd.items() if c <= 1]
    for a in _expired:
        _sl_cd.pop(a)
    for a in list(_sl_cd.keys()):
        _sl_cd[a] -= 1

    # Verificar pausa inteligente (pode retornar 0.0 = pausado, ou fator parcial)
    _pause_mult = _check_smart_pause(_today_pnl, _week_pnl, capital, _all_mom_scores)

    if _pause_mult <= 0.0:
        # Bot PAUSADO — não conta o PnL desse ciclo
        no_position_reason = f"Pausado por proteção: {_protection_state.get('pause_reason', 'risco elevado')}"
        _trade_log("PAUSED", "—", capital,
            f"⏸️ Bot pausado: {_protection_state['pause_reason']} | Aguardando sinais fortes para retomar...")
        cycle_pnl = 0.0
        pnl_5m = pnl_1h = pnl_1d = 0.0
    elif _pause_mult < 1.0:
        # Operando com tamanho reduzido (já aplicado via size_multiplier no _calc_pnl_bucket)
        _trade_log("REDUCED", "—", capital,
            f"📉 Operando com {_pause_mult*100:.0f}% do tamanho | Diário: R$ {_today_pnl:.2f} | Semanal: R$ {_week_pnl:.2f}")

    # ── 5. Montar posições unificadas ────────────────────────────────────
    prev_positions = dict(_trade_state.get("positions", {}))  # snapshot antes
    new_positions = {}
    for asset, info in pos_5m.items():
        new_positions[asset] = {"amount": info["amount"], "action": "BUY", "tf": "5m",
            "pct": round(info["amount"]/capital*100, 1), "classification": "SHORT", "change_pct": info["ret_pct"]}
    for asset, info in pos_1h.items():
        if asset in new_positions:
            new_positions[asset]["amount"] = round(new_positions[asset]["amount"] + info["amount"], 2)
            new_positions[asset]["tf"] += "+1h"
        else:
            new_positions[asset] = {"amount": info["amount"], "action": "BUY", "tf": "1h",
                "pct": round(info["amount"]/capital*100, 1), "classification": "MEDIUM", "change_pct": info["ret_pct"]}
    for asset, info in pos_1d.items():
        if asset in new_positions:
            new_positions[asset]["amount"] = round(new_positions[asset]["amount"] + info["amount"], 2)
            new_positions[asset]["tf"] += "+1d"
        else:
            new_positions[asset] = {"amount": info["amount"], "action": "BUY", "tf": "1d",
                "pct": round(info["amount"]/capital*100, 1), "classification": "LONG", "change_pct": info["ret_pct"]}
    for asset, info in pos_mr.items():
        if asset in new_positions:
            new_positions[asset]["amount"] = round(new_positions[asset]["amount"] + info["amount"], 2)
            new_positions[asset]["tf"] += "+mr"
        else:
            new_positions[asset] = {"amount": info["amount"], "action": "BUY", "tf": "mr",
                "pct": round(info["amount"]/capital*100, 1), "classification": "REVERSION", "change_pct": info["ret_pct"]}
    for asset, info in pos_bo.items():
        if asset in new_positions:
            new_positions[asset]["amount"] = round(new_positions[asset]["amount"] + info["amount"], 2)
            new_positions[asset]["tf"] += "+bo"
        else:
            new_positions[asset] = {"amount": info["amount"], "action": "BUY", "tf": "bo",
                "pct": round(info["amount"]/capital*100, 1), "classification": "BREAKOUT", "change_pct": info["ret_pct"]}
    for asset, info in pos_sq.items():
        if asset in new_positions:
            new_positions[asset]["amount"] = round(new_positions[asset]["amount"] + info["amount"], 2)
            new_positions[asset]["tf"] += "+sq"
        else:
            sq_dir = sq_results.get(asset, {}).get("direction", "LONG")
            new_positions[asset] = {"amount": info["amount"], "action": "BUY", "tf": "sq",
                "pct": round(info["amount"]/capital*100, 1), "classification": f"SQUEEZE_{sq_dir}", "change_pct": info["ret_pct"]}
    for asset, info in pos_ls.items():
        if asset in new_positions:
            new_positions[asset]["amount"] = round(new_positions[asset]["amount"] + info["amount"], 2)
            new_positions[asset]["tf"] += "+ls"
        else:
            ls_dir = ls_results.get(asset, {}).get("direction", "LONG")
            new_positions[asset] = {"amount": info["amount"], "action": "BUY", "tf": "ls",
                "pct": round(info["amount"]/capital*100, 1), "classification": f"SWEEP_{ls_dir}", "change_pct": info["ret_pct"]}
    for asset, info in pos_fvg.items():
        if asset in new_positions:
            new_positions[asset]["amount"] = round(new_positions[asset]["amount"] + info["amount"], 2)
            new_positions[asset]["tf"] += "+fvg"
        else:
            fvg_dir = fvg_results.get(asset, {}).get("direction", "LONG")
            new_positions[asset] = {"amount": info["amount"], "action": "BUY", "tf": "fvg",
                "pct": round(info["amount"]/capital*100, 1), "classification": f"FVG_{fvg_dir}", "change_pct": info["ret_pct"]}
    for asset, info in pos_vr.items():
        if asset in new_positions:
            new_positions[asset]["amount"] = round(new_positions[asset]["amount"] + info["amount"], 2)
            new_positions[asset]["tf"] += "+vr"
        else:
            vr_dir = vr_results.get(asset, {}).get("direction", "LONG")
            new_positions[asset] = {"amount": info["amount"], "action": "BUY", "tf": "vr",
                "pct": round(info["amount"]/capital*100, 1), "classification": f"VWAP_REV_{vr_dir}", "change_pct": info["ret_pct"]}
    for asset, info in pos_pb.items():
        if asset in new_positions:
            new_positions[asset]["amount"] = round(new_positions[asset]["amount"] + info["amount"], 2)
            new_positions[asset]["tf"] += "+pb"
        else:
            pb_dir = pb_results.get(asset, {}).get("direction", "LONG")
            _pb_lvl = pb_results.get(asset, {}).get("pyramid_level", 1)
            new_positions[asset] = {"amount": info["amount"], "action": "BUY", "tf": "pb",
                "pct": round(info["amount"]/capital*100, 1), "classification": f"PYRAMID_BO_{pb_dir}_L{_pb_lvl}", "change_pct": info["ret_pct"]}

    # Preserva entry_time e entry_price das posições anteriores (não sobrescreve)
    for asset, pos in new_positions.items():
        prev = prev_positions.get(asset, {})
        if isinstance(prev, dict):
            pos["entry_time"]  = prev.get("entry_time",  _brt_now().isoformat())
            pos["entry_price"] = prev.get("entry_price", pos.get("entry_price", None))
        else:
            pos["entry_time"]  = _brt_now().isoformat()
            pos["entry_price"] = pos.get("entry_price", None)

    _trade_state["positions"] = new_positions
    if len(new_positions) == 0:
        _trade_state["last_no_position_reason"] = no_position_reason or "Sem oportunidade com risco/retorno aceitável neste ciclo."
    else:
        _trade_state["last_no_position_reason"] = ""
    _trade_state["last_cycle"] = _brt_now().isoformat()
    _trade_state["total_pnl"] = round(_trade_state.get("total_pnl", 0.0) + cycle_pnl, 4)

    # ── 5a. Executar ordens nos brokers (paper/live) ──────────────────
    if MARKET_DATA_AVAILABLE and market_data_service and _pause_mult > 0:
        # Novas entradas: ativos em new_positions que não estavam antes
        entries = {a: info for a, info in new_positions.items() if a not in prev_positions}
        # Saídas: ativos que saíram do portfólio
        exits = {a: info for a, info in prev_positions.items() if a not in new_positions}

        _order_count = 0
        for asset, info in entries.items():
            try:
                prices = klines_by_tf.get("5m", {}).get(asset, {}).get("prices", [])
                entry_price = prices[-1] if prices else 0
                if entry_price <= 0:
                    continue
                # Validação de valor mínimo de ordem (rejeita se abaixo do mín da corretora)
                notional_brl = info["amount"]
                if market_data_service._is_crypto(asset):
                    usd_rate = getattr(settings, "USD_BRL_RATE", 5.75)
                    try:
                        lr = await market_data_service.get_usd_brl_rate()
                        if lr and lr > 0:
                            usd_rate = lr
                    except Exception:
                        pass
                    notional_usd = notional_brl / usd_rate
                    if notional_usd < settings.MIN_NOTIONAL_BINANCE_USD:
                        print(f"[trade] Skip {asset}: ${notional_usd:.2f} < mín Binance ${settings.MIN_NOTIONAL_BINANCE_USD}", flush=True)
                        continue
                elif market_data_service._is_b3(asset):
                    if notional_brl < settings.MIN_NOTIONAL_BTG_BRL:
                        print(f"[trade] Skip {asset}: R${notional_brl:.2f} < mín BTG R${settings.MIN_NOTIONAL_BTG_BRL}", flush=True)
                        continue
                else:
                    if notional_brl < settings.MIN_NOTIONAL_DEFAULT_BRL:
                        print(f"[trade] Skip {asset}: R${notional_brl:.2f} < mín R${settings.MIN_NOTIONAL_DEFAULT_BRL}", flush=True)
                        continue
                # Quantidade: valor alocado / preço
                quantity = round(info["amount"] / entry_price, 8)
                if quantity <= 0:
                    continue
                result = await market_data_service.place_order(asset, "buy", quantity, entry_price, "market")
                if result and result.get("status") not in ("REJECTED", "rejected"):
                    # Guardar preço de entrada na posição
                    new_positions[asset]["entry_price"] = entry_price
                    new_positions[asset]["order_id"] = result.get("order_id", "")
                    currency = "R$" if market_data_service._is_b3(asset) else "$"
                    _trade_log("ENTRY", asset, info["amount"],
                        f"📈 COMPRA {asset}: {quantity:.6f} @ {currency}{entry_price:.4f} = {currency}{info['amount']:.2f} [{info.get('tf', '?')}]")
                    _order_count += 1
                    # ── Coloca stop loss SERVER-SIDE na corretora ────────────────────────
                    # A ordem fica ativa na corretora mesmo se o bot reiniciar
                    stop_pct = getattr(settings, "STOP_LOSS_PERCENTAGE", 0.02)
                    asyncio.create_task(
                        market_data_service.place_stop_loss_order(asset, quantity, entry_price, stop_pct)
                    )
                    # Alerta
                    if ALERTS_AVAILABLE and alert_manager:
                        asyncio.create_task(alert_manager.alert_trade_executed(asset, "BUY", quantity, entry_price))
            except Exception as e:
                print(f"[trade] Erro entry order {asset}: {e}", flush=True)

        for asset, info in exits.items():
            try:
                prices = klines_by_tf.get("5m", {}).get(asset, {}).get("prices", [])
                exit_price = prices[-1] if prices else 0
                prev_amt = info.get("amount", 0)
                if exit_price <= 0 or prev_amt <= 0:
                    continue
                quantity = round(prev_amt / exit_price, 8)
                if quantity <= 0:
                    continue
                result = await market_data_service.place_order(asset, "sell", quantity, exit_price, "market")
                if result and result.get("status") not in ("REJECTED", "rejected"):
                    entry_px = info.get("entry_price", exit_price)
                    trade_pnl = round((exit_price - entry_px) / entry_px * prev_amt, 4) if entry_px > 0 else 0
                    currency = "R$" if market_data_service._is_b3(asset) else "$"
                    _trade_log("EXIT", asset, prev_amt,
                        f"📉 VENDA {asset}: {quantity:.6f} @ {currency}{exit_price:.4f} | P&L: {currency}{trade_pnl:+.4f}")
                    _order_count += 1
                    # Alerta
                    if ALERTS_AVAILABLE and alert_manager:
                        asyncio.create_task(alert_manager.alert_trade_executed(asset, "SELL", quantity, exit_price))
                    # Alerta de stop loss
                    if trade_pnl < 0 and ALERTS_AVAILABLE and alert_manager:
                        loss_pct = round((exit_price - entry_px) / entry_px * 100, 2) if entry_px > 0 else 0
                        asyncio.create_task(alert_manager.alert_stop_loss_triggered(asset, exit_price, loss_pct))
            except Exception as e:
                print(f"[trade] Erro exit order {asset}: {e}", flush=True)

        if _order_count > 0:
            print(f"[trade] 📨 {_order_count} ordens executadas (entries={len(entries)}, exits={len(exits)})", flush=True)

    # ── 5c. Alerta de risco elevado ──────────────────────────────────
    if ALERTS_AVAILABLE and alert_manager and irq_score > 0.70:
        asyncio.create_task(alert_manager.alert_risk_level(irq_score, {a: {"amount": i["amount"]} for a, i in new_positions.items()}))

    # ── 5b. Sincronizar risk_manager com dados do ciclo ──────────────────
    try:
        risk_manager.daily_pnl = round(risk_manager.daily_pnl + cycle_pnl, 4)
        # Registrar posições no risk_manager para stop loss/take profit
        risk_manager.positions.clear()
        for asset, info in new_positions.items():
            prices = klines_by_tf.get("5m", {}).get(asset, {}).get("prices", [])
            entry_price = prices[-1] if prices else 0
            if entry_price > 0:
                risk_manager.register_position(asset, entry_price, info["amount"])
        # Registrar trade para controle de limites
        risk_manager.record_trade("CYCLE", "BUY", capital, cycle_pnl)
    except Exception as e:
        print(f"[trade] Erro sync risk_manager: {e}", flush=True)

    # ── 6. Log e histórico ───────────────────────────────────────────────
    turbo_active = _is_turbo_mode(klines_by_tf["5m"])
    mr_info  = f" | MR: R${pnl_mr:+.2f}" if top_mr else ""
    bo_info  = f" | BO: R${pnl_bo:+.2f}" if top_bo else ""
    sq_info  = f" | SQ: R${pnl_sq:+.2f}" if top_sq else ""
    ls_info  = f" | LS: R${pnl_ls:+.2f}" if top_ls else ""
    fvg_info = f" | FVG: R${pnl_fvg:+.2f}" if top_fvg else ""
    vr_info  = f" | VR: R${pnl_vr:+.2f}" if top_vr else ""
    pb_info  = f" | PB: R${pnl_pb:+.2f}" if top_pb else ""
    _trade_log("CICLO", "—", capital,
        f"🔄 [{session_label}] {data_source} | 5m: R${pnl_5m:+.2f} | 1h: R${pnl_1h:+.2f} | 1d: R${pnl_1d:+.2f}{mr_info}{bo_info}{sq_info}{ls_info}{fvg_info}{vr_info}{pb_info} | Grid: R${grid_pnl:+.2f} | Total: R${cycle_pnl:+.2f} | IRQ: {irq_score:.3f} | Regime: {_adx_regime}(ADX={_adx_val:.1f}/ATR={_atr_ratio:.2f}/H={_hurst:.2f}) | Turbo: {'ON' if turbo_active else 'OFF'}")

    _record_cycle_performance(cycle_pnl, capital, irq_score, pnl_5m, pnl_1h, pnl_1d, cycle_costs,
                               pnl_mr=pnl_mr, pnl_bo=pnl_bo, pnl_sq=pnl_sq,
                               pnl_ls=pnl_ls, pnl_fvg=pnl_fvg,
                               pnl_vr=pnl_vr, pnl_pb=pnl_pb)

    # ── ML: Salvar amostras de treinamento (features + outcome por ativo) ─
    try:
        if DB_AVAILABLE and db:
            _ml_ts = _brt_now().isoformat()
            _ml_hour = _brt_now().hour
            _ml_dow = _brt_now().weekday()

            def _save_bucket_ml(pos_dict, mom_dict, tf_name):
                for _asset, _pos in pos_dict.items():
                    _mom = mom_dict.get(_asset, {})
                    db.save_ml_sample(
                        cycle_ts=_ml_ts,
                        asset=_asset,
                        tf=tf_name,
                        momentum_score=float(_mom.get("momentum_score", 0)),
                        roc_score=float(_mom.get("return_score", 0)),
                        rsi_score=float(_mom.get("rsi_score", 0)),
                        trend_score=float(_mom.get("trend_score", 0)),
                        volume_score=float(_mom.get("volume_score", 0)),
                        candle_score=float(_mom.get("macd_score", 0)),
                        signal_quality=float(_mom.get("signal_quality", 0)),
                        irq_score=float(irq_score),
                        hour_of_day=int(_ml_hour),
                        day_of_week=int(_ml_dow),
                        atr_sl=float(_pos.get("atr_sl", 0)) / 100.0,
                        atr_tp=float(_pos.get("atr_tp", 0)) / 100.0,
                        vol_mult=float(_pos.get("vol_mult", 1.0)),
                        mom_accel=float(_pos.get("mom_accel", 1.0)),
                        ret_pct=float(_pos.get("ret_pct", 0)) / 100.0,
                        net_pnl=float(_pos.get("net_pnl", 0)),
                        label=1 if float(_pos.get("net_pnl", 0)) > 0 else 0,
                    )

            _save_bucket_ml(pos_5m, mom_5m, "5m")
            _save_bucket_ml(pos_1h, mom_1h, "1h")
            _save_bucket_ml(pos_1d, mom_1d, "1d")
            _save_bucket_ml(pos_vr, mom_vr, "vr")
            _save_bucket_ml(pos_pb, mom_pb, "pb")
    except Exception as _ml_err:
        print(f"[ml] Erro ao salvar amostra: {_ml_err}", flush=True)

    # ── Atualizar capital com PnL líquido do ciclo ──────────────────────
    if cycle_pnl != 0:
        new_capital = round(_trade_state["capital"] + cycle_pnl, 2)
        print(f"[trade] Capital: R${_trade_state['capital']:.2f} {cycle_pnl:+.2f} → R${new_capital:.2f}", flush=True)
        _trade_state["capital"] = new_capital

    # ✨ Notificação WhatsApp por ciclo (só se houve PnL relevante)
    if ALERTS_AVAILABLE and alert_manager:
        asyncio.create_task(alert_manager.alert_cycle_result(
            cycle_pnl=cycle_pnl,
            today_pnl=_today_pnl,
            capital=_trade_state["capital"],
            positions_count=len(new_positions),
            fees=abs(fees_total),
            irq=irq_score,
            session_label=session_label
        ))

    if DB_AVAILABLE:
        try:
            db.save_analysis("trade_cycle", {"irq": irq_score, "capital": capital, "cycle_pnl": cycle_pnl}, irq_score)
        except Exception:
            pass

    return {
        "positions":   new_positions,
        "irq":         round(irq_score, 4),
        "irq_level":   protection["level"],
        "assets_analyzed": len(new_positions),
        "last_cycle":  _trade_state["last_cycle"],
        "data_source": data_source,
        "session":     session_label,
        "b3_open":     b3_open,
        "cycle_pnl":   cycle_pnl,
        "gross_cycle_pnl": gross_cycle_pnl,
        "pnl_5m":      pnl_5m,
        "pnl_1h":      pnl_1h,
        "pnl_1d":      pnl_1d,
        "pnl_mr":      pnl_mr,
        "pnl_bo":      pnl_bo,
        "pnl_sq":      pnl_sq,
        "pnl_ls":      pnl_ls,
        "pnl_fvg":     pnl_fvg,
        "costs": {
            "total": round(cycle_costs.get("total", 0.0), 4),
            "brokerage": round(cycle_costs.get("brokerage", 0.0), 4),
            "exchange_fees": round(cycle_costs.get("exchange_fees", 0.0), 4),
            "spread": round(cycle_costs.get("spread", 0.0), 4),
            "slippage": round(cycle_costs.get("slippage", 0.0), 4),
            "fx": round(cycle_costs.get("fx", 0.0), 4),
            "min_fee_adj": round(cycle_costs.get("min_fee_adj", 0.0), 4),
            "by_market": {k: round(v, 4) for k, v in cycle_costs.get("by_market", {}).items()},
            "by_timeframe": {
                "5m": round((costs_5m or {}).get("total", 0.0), 4),
                "1h": round((costs_1h or {}).get("total", 0.0), 4),
                "1d": round((costs_1d or {}).get("total", 0.0), 4),
            },
        },
        "capital_5m":  capital_5m,
        "capital_1h":  capital_1h,
        "capital_1d":  capital_1d,
        "capital_mr":  capital_mr,
        "capital_bo":  capital_bo,
        "capital_sq":  capital_sq,
        "capital_ls":  capital_ls,
        "capital_fvg": capital_fvg,
        # Regime v3.0 — multi-sinal
        "adx_value":      round(_adx_val, 1),
        "adx_regime":     _adx_regime,
        "atr_ratio":      round(_atr_ratio, 3),
        "hurst":          round(_hurst, 3),
        "regime_confidence": round(_regime_conf, 3),
        "regime_signals": _regime_result.get("signals", {}),
        "dyn_weights":    _dyn_weights,
        # Grid Trading
        "grid_pnl":    grid_pnl,
        "grid_assets":  len(grid_details),
        # Simulation mode (stress testing)
        "sim_mode":    _sim_mode,
        # Turbo & Estratégias
        "turbo_active": turbo_active,
        "strategies_active": {
            "atr_adaptive": True,
            "grid_trading": settings.GRID_ENABLED,
            "scalping_turbo": turbo_active,
            "volume_confirm": settings.VOLUME_CONFIRM_ENABLED,
            "partial_tp": settings.PARTIAL_TP_ENABLED,
            "momentum_accel": settings.MOMENTUM_ACCEL_ENABLED,
            "mean_reversion": len(top_mr) > 0,
            "breakout": len(top_bo) > 0,
            "squeeze": len(top_sq) > 0,
            "liquidity_sweep": len(top_ls) > 0,
            "fvg": len(top_fvg) > 0,
            "adaptive_alloc": _strategy_state.get("dynamic_alloc_enabled", False),
            "regime_filter": _strategy_state.get("regime_filter_enabled", False),
            "tf_risk_tuning": _strategy_state.get("tf_risk_tuning_enabled", False),
        },
        "strategy": {
            "regime": strategy_regime,
            "reason": strategy_reason,
            "alloc_base_pct": {
                "5m": round(_TIMEFRAME_ALLOC["5m"] * 100, 1),
                "1h": round(_TIMEFRAME_ALLOC["1h"] * 100, 1),
                "1d": round(_TIMEFRAME_ALLOC["1d"] * 100, 1),
            },
            "alloc_effective_pct": {
                "5m": round(effective_alloc["5m"] * 100, 1),
                "1h": round(effective_alloc["1h"] * 100, 1),
                "1d": round(effective_alloc["1d"] * 100, 1),
            },
            "recent_tf_pnl": strategy_tf_recent,
        },
        # Proteção inteligente
        "protection": {
            "paused": _protection_state["paused"],
            "hard_stopped": _protection_state["hard_stopped"],
            "consecutive_losses": _protection_state["consecutive_losses"],
            "size_multiplier": _protection_state["size_multiplier"],
            "peak_capital": _protection_state["peak_capital"],
            "pause_reason": _protection_state.get("pause_reason", ""),
            "daily_pnl": round(_today_pnl, 2),
            "weekly_pnl": round(_week_pnl, 2),
            "drawdown_pct": round(((_protection_state["peak_capital"] - capital) / _protection_state["peak_capital"]) * 100, 2) if _protection_state["peak_capital"] > 0 else 0,
        },
    }




# ═══════════════════════════════════════════
# ENDPOINTS: SCHEDULER (CONTROLE DO LOOP AUTOMÁTICO)
# ═══════════════════════════════════════════

@app.get("/scheduler/status")
async def scheduler_status():
    """Retorna o estado do scheduler automático."""
    return {
        "success": True,
        "data": {
            "running":            _scheduler_state["running"],
            "interval_minutes":   _scheduler_state["interval_minutes"],
            "only_market_hours":  _scheduler_state["only_market_hours"],
            "total_auto_cycles":  _effective_total_cycles(),
            "market_open_now":    _is_market_open(),
            "session":            _scheduler_state.get("session", _current_session()[1]),
            "b3_open":            _is_market_open(),
            "crypto_always_on":   True,
            "recent_errors":      _last_scheduler_errors[-5:],
            "debug":              _scheduler_debug,
        },
    }


@app.post("/scheduler/start")
async def scheduler_start(body: dict = None):
    """Inicia (ou reinicia) o scheduler automático."""
    body = body or {}
    if "interval_minutes" in body:
        minutes = int(body["interval_minutes"])
        if minutes < 1:
            raise HTTPException(status_code=400, detail="interval_minutes deve ser >= 1")
        _scheduler_state["interval_minutes"] = minutes
    if "only_market_hours" in body:
        _scheduler_state["only_market_hours"] = bool(body["only_market_hours"])

    # Cancelar task anterior se existir
    old_task = _scheduler_state.get("task")
    if old_task and not old_task.done():
        _scheduler_state["running"] = False
        old_task.cancel()
        try:
            await old_task
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_auto_cycle_loop())
    _scheduler_state["task"] = task
    _persist_scheduler_state()
    _trade_log("SCHEDULER", "—", 0,
        f"▶️ Scheduler INICIADO — ciclo a cada {_scheduler_state['interval_minutes']} min")
    return {
        "success": True,
        "message": f"Scheduler iniciado (ciclo a cada {_scheduler_state['interval_minutes']} min)",
        "running": True,
    }


@app.post("/scheduler/stop")
async def scheduler_stop():
    """Para o scheduler automático."""
    _scheduler_state["running"] = False
    task = _scheduler_state.get("task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _persist_scheduler_state()
    _trade_log("SCHEDULER", "—", 0, "⏹️ Scheduler PARADO")
    return {"success": True, "message": "Scheduler parado", "running": False}


@app.post("/scheduler/config")
async def scheduler_config(body: dict):
    """
    Configura o intervalo e horário de mercado sem reiniciar.

    Body:
        interval_minutes: int   (ex: 15, 30, 60)
        only_market_hours: bool (true = só opera seg-sex 10h-17h BRT)
    """
    if "interval_minutes" in body:
        minutes = int(body["interval_minutes"])
        if minutes < 1:
            raise HTTPException(status_code=400, detail="interval_minutes deve ser >= 1")
        _scheduler_state["interval_minutes"] = minutes
    if "only_market_hours" in body:
        _scheduler_state["only_market_hours"] = bool(body["only_market_hours"])
    _persist_scheduler_state()
    return {
        "success": True,
        "data": {
            "interval_minutes":  _scheduler_state["interval_minutes"],
            "only_market_hours": _scheduler_state["only_market_hours"],
        },
    }


# ═══════════════════════════════════════════
# ENDPOINTS: PERFORMANCE & BACKTEST
# ═══════════════════════════════════════════

@app.get("/performance")
async def get_performance():
    """
    Retorna métricas de performance acumuladas desde o primeiro ciclo:
    P&L total, win rate, melhor/pior ciclo, equity curve, Sharpe estimado.
    """
    cycles = _perf_state.get("cycles", [])
    equity = _perf_state.get("total_pnl_history", [])
    wins   = _perf_state.get("win_count", 0)
    losses = _perf_state.get("loss_count", 0)
    total  = wins + losses
    # Offsets históricos (preservados após migração ou restart sem histórico)
    _cycles_offset = int(_perf_state.get("total_cycles_offset", 0))
    _pnl_offset    = float(_perf_state.get("total_pnl_offset", 0.0))

    # Calcular Sharpe dos ciclos
    pnls = [c["pnl"] for c in cycles]
    if len(pnls) >= 2:
        import statistics as _stats
        mu    = _stats.mean(pnls)
        sigma = _stats.stdev(pnls)
        sharpe = (mu / sigma) * (252 ** 0.5) if sigma > 0 else 0.0
    else:
        sharpe = 0.0

    # Usa P&L histórico persistido quando não há ciclos carregados em memória
    total_pnl = sum(pnls) + _pnl_offset if pnls else _pnl_offset
    avg_daily = total_pnl / _effective_total_cycles() if _effective_total_cycles() else 0.0

    # Max drawdown da equity curve
    max_dd = 0.0
    if len(equity) > 1:
        peak = equity[0]
        for v in equity:
            peak = max(peak, v)
            dd = (v - peak) / peak * 100 if peak > 0 else 0
            max_dd = min(max_dd, dd)

    # P&L por timeframe — hoje e total (horário BRT UTC-3)
    from datetime import timezone as _tz, timedelta as _td
    _brt = _tz(_td(hours=-3))
    today_str = datetime.now(_brt).strftime("%Y-%m-%d")
    today_cycles = [c for c in cycles if c.get("timestamp", "").startswith(today_str)]
    pnl_today_5m  = round(sum(c.get("pnl_5m", 0) for c in today_cycles), 2)
    pnl_today_1h  = round(sum(c.get("pnl_1h", 0) for c in today_cycles), 2)
    pnl_today_1d  = round(sum(c.get("pnl_1d", 0) for c in today_cycles), 2)
    pnl_today     = round(pnl_today_5m + pnl_today_1h + pnl_today_1d, 2)
    today_gain    = round(sum(c.get("pnl", 0) for c in today_cycles if c.get("pnl", 0) > 0), 2)
    today_loss    = round(sum(abs(c.get("pnl", 0)) for c in today_cycles if c.get("pnl", 0) < 0), 2)
    pnl_total_5m  = round(sum(c.get("pnl_5m", 0) for c in cycles), 2)
    pnl_total_1h  = round(sum(c.get("pnl_1h", 0) for c in cycles), 2)
    pnl_total_1d  = round(sum(c.get("pnl_1d", 0) for c in cycles), 2)
    costs_today_total = round(sum(c.get("fees_total", 0) or 0 for c in today_cycles), 4)
    costs_today_brokerage = round(sum(c.get("fees_brokerage", 0) or 0 for c in today_cycles), 4)
    costs_today_exchange = round(sum(c.get("fees_exchange", 0) or 0 for c in today_cycles), 4)
    costs_today_spread = round(sum(c.get("fees_spread", 0) or 0 for c in today_cycles), 4)
    costs_today_slippage = round(sum(c.get("fees_slippage", 0) or 0 for c in today_cycles), 4)
    costs_today_fx = round(sum(c.get("fees_fx", 0) or 0 for c in today_cycles), 4)

    costs_total = round(_perf_state.get("total_fees") or sum(c.get("fees_total", 0) or 0 for c in cycles), 4)
    costs_total_brokerage = round(_perf_state.get("total_brokerage") or sum(c.get("fees_brokerage", 0) or 0 for c in cycles), 4)
    costs_total_exchange = round(_perf_state.get("total_exchange_fees") or sum(c.get("fees_exchange", 0) or 0 for c in cycles), 4)
    costs_total_spread = round(_perf_state.get("total_spread") or sum(c.get("fees_spread", 0) or 0 for c in cycles), 4)
    costs_total_slippage = round(_perf_state.get("total_slippage") or sum(c.get("fees_slippage", 0) or 0 for c in cycles), 4)
    costs_total_fx = round(_perf_state.get("total_fx") or sum(c.get("fees_fx", 0) or 0 for c in cycles), 4)
    # totais acumulados (da memória persistida, com fallback do cálculo instantâneo)
    total_gain_acc = _perf_state.get("total_gain") or round(sum(c.get("pnl", 0) for c in cycles if c.get("pnl", 0) > 0), 2)
    total_loss_acc = _perf_state.get("total_loss") or round(sum(abs(c.get("pnl", 0)) for c in cycles if c.get("pnl", 0) < 0), 2)

    # ── Métricas de diagnóstico avançadas ─────────────────────────────────
    profit_factor = round(total_gain_acc / total_loss_acc, 3) if total_loss_acc > 0 else 0.0
    avg_win  = round(total_gain_acc / wins, 2)   if wins   > 0 else 0.0
    avg_loss = round(total_loss_acc / losses, 2) if losses > 0 else 0.0
    risk_reward_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0

    # Per-bucket PnL totais (ciclos com os novos campos pnl_mr/bo/sq/ls/fvg)
    pnl_total_mr  = round(sum(c.get("pnl_mr",  0) or 0 for c in cycles), 2)
    pnl_total_bo  = round(sum(c.get("pnl_bo",  0) or 0 for c in cycles), 2)
    pnl_total_sq  = round(sum(c.get("pnl_sq",  0) or 0 for c in cycles), 2)
    pnl_total_ls  = round(sum(c.get("pnl_ls",  0) or 0 for c in cycles), 2)
    pnl_total_fvg = round(sum(c.get("pnl_fvg", 0) or 0 for c in cycles), 2)
    pnl_total_vr  = round(sum(c.get("pnl_vr",  0) or 0 for c in cycles), 2)
    pnl_total_pb  = round(sum(c.get("pnl_pb",  0) or 0 for c in cycles), 2)

    # Per-bucket win rates (ciclos onde o bucket gerou pnl > 0)
    def _bucket_wr(key: str) -> float:
        vals = [c.get(key, 0) or 0 for c in cycles if (c.get(key, 0) or 0) != 0]
        if not vals:
            return 0.0
        return round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1)

    # Readiness Score 0-100 — quão próximo de capital real
    # Critérios: PF>=1.5(25pts) + WR>=40%(20pts) + Cycles>=1000(20pts) + Sharpe>=1.5(20pts) + MaxDD>-10%(15pts)
    _rs_pf    = 25 if profit_factor >= 1.5 else round(profit_factor / 1.5 * 25, 1)
    _rs_wr    = 20 if (wins/total*100 if total > 0 else 0) >= 40 else round((wins/total*100 if total > 0 else 0) / 40 * 20, 1)
    _rs_cyc   = 20 if _effective_total_cycles() >= 1000 else round(_effective_total_cycles() / 1000 * 20, 1)
    _rs_sh    = 20 if sharpe >= 1.5 else round(max(sharpe, 0) / 1.5 * 20, 1)
    _rs_dd    = 15 if max_dd > -10 else round(max(0, (max_dd + 20) / 10 * 15), 1)
    readiness_score = round(_rs_pf + _rs_wr + _rs_cyc + _rs_sh + _rs_dd, 1)
    readiness_label = (
        "PRONTO" if readiness_score >= 80
        else "QUASE" if readiness_score >= 60
        else "EM PROGRESSO" if readiness_score >= 40
        else "CEDO DEMAIS"
    )

    return {
        "success": True,
        "data": {
            "total_cycles":      _effective_total_cycles(),
            "win_count":         wins,
            "loss_count":        losses,
            "win_rate_pct":      round(wins / total * 100, 2) if total > 0 else 0.0,
            "total_pnl":         round(total_pnl, 2),
            "avg_pnl_per_cycle": round(avg_daily, 2),
            "best_cycle_pnl":    _perf_state.get("best_day_pnl", 0.0),
            "worst_cycle_pnl":   _perf_state.get("worst_day_pnl", 0.0),
            "max_drawdown_pct":  round(max_dd, 4),
            "sharpe_ratio":      round(sharpe, 4),
            "equity_curve":      equity[-100:],
            "recent_cycles":     cycles[-20:],
            "last_backtest":     _perf_state.get("last_backtest"),
            "current_capital":   _trade_state.get("capital"),
            # P&L por timeframe — hoje
            "pnl_today":         pnl_today,
            "pnl_today_5m":      pnl_today_5m,
            "pnl_today_1h":      pnl_today_1h,
            "pnl_today_1d":      pnl_today_1d,
            "today_cycles":      len(today_cycles),
            # Ganho e perda separados — hoje
            "today_gain":        today_gain,
            "today_loss":        today_loss,
            # Ganho e perda separados — acumulado total
            "total_gain":        round(total_gain_acc, 2),
            "total_loss":        round(total_loss_acc, 2),
            # Métricas de diagnóstico avançadas
            "profit_factor":     profit_factor,
            "avg_win":           avg_win,
            "avg_loss":          avg_loss,
            "risk_reward_ratio": risk_reward_ratio,
            # Per-bucket PnL e win rate
            "pnl_total_mr":      pnl_total_mr,
            "pnl_total_bo":      pnl_total_bo,
            "pnl_total_sq":      pnl_total_sq,
            "pnl_total_ls":      pnl_total_ls,
            "pnl_total_fvg":     pnl_total_fvg,
            "pnl_total_vr":      pnl_total_vr,
            "pnl_total_pb":      pnl_total_pb,
            "wr_mr_pct":         _bucket_wr("pnl_mr"),
            "wr_bo_pct":         _bucket_wr("pnl_bo"),
            "wr_sq_pct":         _bucket_wr("pnl_sq"),
            "wr_ls_pct":         _bucket_wr("pnl_ls"),
            "wr_fvg_pct":        _bucket_wr("pnl_fvg"),
            "wr_vr_pct":         _bucket_wr("pnl_vr"),
            "wr_pb_pct":         _bucket_wr("pnl_pb"),
            # Readiness Assessment
            "readiness_score":   readiness_score,
            "readiness_label":   readiness_label,
            "readiness_breakdown": {
                "profit_factor":  _rs_pf,
                "win_rate":       _rs_wr,
                "cycles":         _rs_cyc,
                "sharpe":         _rs_sh,
                "drawdown":       _rs_dd,
            },
            # Custos operacionais (simulação realista)
            "costs_today_total": costs_today_total,
            "costs_today_brokerage": costs_today_brokerage,
            "costs_today_exchange": costs_today_exchange,
            "costs_today_spread": costs_today_spread,
            "costs_today_slippage": costs_today_slippage,
            "costs_today_fx": costs_today_fx,
            "costs_total": costs_total,
            "costs_total_brokerage": costs_total_brokerage,
            "costs_total_exchange": costs_total_exchange,
            "costs_total_spread": costs_total_spread,
            "costs_total_slippage": costs_total_slippage,
            "costs_total_fx": costs_total_fx,
            "cost_model_assumptions": {
                "b3": _TRADING_COST_MODEL["b3"],
                "us": _TRADING_COST_MODEL["us"],
                "crypto": _TRADING_COST_MODEL["crypto"],
                "forex": _TRADING_COST_MODEL["forex"],
                "commodity": _TRADING_COST_MODEL["commodity"],
            },
            # P&L por timeframe — histórico total
            "pnl_total_5m":      pnl_total_5m,
            "pnl_total_1h":      pnl_total_1h,
            "pnl_total_1d":      pnl_total_1d,
            # Alocação vigente
            "alloc_5m_pct":      int(_TIMEFRAME_ALLOC["5m"] * 100),
            "alloc_1h_pct":      int(_TIMEFRAME_ALLOC["1h"] * 100),
            "alloc_1d_pct":      int(_TIMEFRAME_ALLOC["1d"] * 100),
            "alloc_mr_pct":      int(_MR_ALLOC_PCT * 100),
            "alloc_bo_pct":      int(_BO_ALLOC_PCT * 100),
            "alloc_sq_pct":      int(_SQ_ALLOC_PCT * 100),
            "alloc_ls_pct":      int(_LS_ALLOC_PCT * 100),
            "alloc_fvg_pct":     int(_FVG_ALLOC_PCT * 100),
            "alloc_vr_pct":      int(_VR_ALLOC_PCT * 100),
            "alloc_pb_pct":      int(_PB_ALLOC_PCT * 100),
        },
    }


@app.get("/performance/history")
async def get_performance_history():
    """
    Retorna PnL agrupado por dia (BRT), com todos os ciclos históricos.
    """
    from datetime import timezone as _tz, timedelta as _td
    from collections import defaultdict
    _brt = _tz(_td(hours=-3))

    cycles = _perf_state.get("cycles", [])

    by_day: dict = defaultdict(lambda: {
        "pnl": 0.0, "pnl_5m": 0.0, "pnl_1h": 0.0, "pnl_1d": 0.0,
        "cycles": 0, "wins": 0, "losses": 0,
        "best_cycle": 0.0, "worst_cycle": 0.0,
    })

    for c in cycles:
        ts = c.get("timestamp", "")
        if not ts:
            continue
        day = ts[:10]
        pnl = c.get("pnl", 0) or 0.0
        by_day[day]["pnl"]     += pnl
        by_day[day]["pnl_5m"]  += c.get("pnl_5m", 0) or 0.0
        by_day[day]["pnl_1h"]  += c.get("pnl_1h", 0) or 0.0
        by_day[day]["pnl_1d"]  += c.get("pnl_1d", 0) or 0.0
        by_day[day]["cycles"]  += 1
        if pnl > 0:
            by_day[day]["wins"] += 1
            if pnl > by_day[day]["best_cycle"]:
                by_day[day]["best_cycle"] = pnl
        elif pnl < 0:
            by_day[day]["losses"] += 1
            if pnl < by_day[day]["worst_cycle"]:
                by_day[day]["worst_cycle"] = pnl

    daily = []
    for day in sorted(by_day.keys()):
        d = by_day[day]
        daily.append({
            "date":        day,
            "pnl":         round(d["pnl"], 2),
            "pnl_5m":      round(d["pnl_5m"], 4),
            "pnl_1h":      round(d["pnl_1h"], 4),
            "pnl_1d":      round(d["pnl_1d"], 4),
            "cycles":      d["cycles"],
            "wins":        d["wins"],
            "losses":      d["losses"],
            "win_rate_pct": round(d["wins"] / d["cycles"] * 100, 1) if d["cycles"] > 0 else 0,
            "best_cycle":  round(d["best_cycle"], 2),
            "worst_cycle": round(d["worst_cycle"], 2),
        })

    total_pnl = sum(c.get("pnl", 0) or 0 for c in cycles) + float(_perf_state.get("total_pnl_offset", 0.0))
    return {
        "success": True,
        "total_cycles": _effective_total_cycles(),
        "total_pnl": round(total_pnl, 2),
        "days": daily,
    }


# ═══════════════════════════════════════════
# ENDPOINTS: SIMULAÇÃO & TESTES
# ═══════════════════════════════════════════

@app.post("/simulate")
async def run_simulation(body: dict = None):
    """
    Executa N ciclos de simulação em sequência com dados reais do Yahoo Finance.
    Retorna equity curve, P&L, win rate e detalhes por ativo.
    """
    body = body or {}
    capital   = float(body.get("capital", 150))
    cycles    = min(int(body.get("cycles", 10)), 100)
    interval  = body.get("interval", "5m")
    limit     = min(int(body.get("limit", 100)), 200)

    equity_curve = [capital]
    events = []
    asset_stats = {}
    wins = 0
    losses = 0
    best_cycle_pnl = 0.0
    worst_cycle_pnl = 0.0
    sim_capital = capital

    try:
        # Buscar dados de mercado uma vez
        all_assets = list(settings.ALL_ASSETS)
        market_data = None

        if MARKET_DATA_AVAILABLE and market_data_service:
            try:
                klines = await market_data_service.get_all_klines(all_assets, interval, limit)
                if klines:
                    market_data = klines
            except Exception:
                pass

        if not market_data:
            market_data = test_assets_data

        for cycle_num in range(1, cycles + 1):
            # Analisar momentum
            momentum_results = MomentumAnalyzer.calculate_multiple_assets(market_data)
            if not momentum_results:
                continue

            momentum_scores = {a: d["momentum_score"] for a, d in momentum_results.items()}

            # Risco
            ref_asset = list(market_data.keys())[0]
            ref_data = market_data[ref_asset]
            risk_analysis = RiskAnalyzer.calculate_irq(
                ref_data.get("prices", []),
                ref_data.get("volumes", []),
            )
            irq_score = risk_analysis["irq_score"]

            # Alocação
            allocation = PortfolioManager.calculate_portfolio_allocation(
                momentum_scores, irq_score, sim_capital,
            )
            rebalancing = PortfolioManager.apply_rebalancing_rules(
                allocation, momentum_results, sim_capital, irq_score,
            )

            # Simular P&L baseado na variação real do último candle
            cycle_pnl = 0.0
            for asset, alloc in rebalancing.items():
                rec = alloc.get("recommended_amount", 0)
                action = alloc.get("action", "HOLD")
                classif = alloc.get("classification", "—")

                if rec > 0:
                    # Pegar variação real do ativo
                    prices = market_data.get(asset, {}).get("prices", [])
                    if len(prices) >= 2:
                        pct_change = (prices[-1] - prices[-2]) / prices[-2]
                    else:
                        pct_change = 0

                    asset_pnl = rec * pct_change
                    cycle_pnl += asset_pnl

                    # Track asset stats
                    if asset not in asset_stats:
                        asset_stats[asset] = {
                            "total_allocated": 0,
                            "times_selected": 0,
                            "avg_score": 0,
                            "classification": classif,
                        }
                    asset_stats[asset]["total_allocated"] += rec
                    asset_stats[asset]["times_selected"] += 1
                    asset_stats[asset]["avg_score"] += momentum_scores.get(asset, 0)
                    asset_stats[asset]["classification"] = classif

                if action in ("BUY", "SELL") and rec > 0:
                    events.append({
                        "cycle": cycle_num,
                        "type": action,
                        "asset": asset,
                        "amount": rec,
                        "classification": classif,
                    })

            sim_capital += cycle_pnl
            equity_curve.append(round(sim_capital, 4))

            if cycle_pnl > 0:
                wins += 1
            elif cycle_pnl < 0:
                losses += 1

            best_cycle_pnl = max(best_cycle_pnl, cycle_pnl)
            worst_cycle_pnl = min(worst_cycle_pnl, cycle_pnl)

        # Calcular médias dos asset stats
        for asset in asset_stats:
            t = asset_stats[asset]["times_selected"]
            if t > 0:
                asset_stats[asset]["avg_score"] /= t
                asset_stats[asset]["avg_score"] = round(asset_stats[asset]["avg_score"], 4)
            asset_stats[asset]["total_allocated"] = round(asset_stats[asset]["total_allocated"], 2)

        total_pnl = sim_capital - capital
        total_cycles = wins + losses

        return {
            "success": True,
            "data": {
                "total_cycles": cycles,
                "final_capital": round(sim_capital, 4),
                "total_pnl": round(total_pnl, 4),
                "win_rate_pct": round(wins / total_cycles * 100, 2) if total_cycles > 0 else 0,
                "avg_pnl_per_cycle": round(total_pnl / cycles, 4) if cycles > 0 else 0,
                "best_cycle": round(best_cycle_pnl, 4),
                "worst_cycle": round(worst_cycle_pnl, 4),
                "equity_curve": equity_curve,
                "asset_summary": asset_stats,
                "events": events[-50:],  # últimos 50 eventos
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/simulate/test-momentum")
async def test_momentum_accuracy():
    """Testa o acerto de direção do motor de momentum com dados reais."""
    try:
        all_assets = list(settings.ALL_ASSETS)
        market_data = None

        if MARKET_DATA_AVAILABLE and market_data_service:
            try:
                klines = await market_data_service.get_all_klines(all_assets, "5m", 60)
                if klines:
                    market_data = klines
            except Exception:
                pass

        if not market_data:
            market_data = test_assets_data

        results = []
        correct = 0
        total = 0

        for asset, data in market_data.items():
            prices = data.get("prices", [])
            volumes = data.get("volumes", [1.0] * len(prices))
            if len(prices) < 10:
                continue

            # Prever com dados até o penúltimo candle
            m = MomentumAnalyzer.calculate_momentum_score(prices[:-1], volumes[:-1])
            predicted_up = m["momentum_score"] > 0
            real_up = prices[-1] > prices[-2]
            is_correct = (predicted_up == real_up)

            # Lateral é considerado acerto se movimento < 0.05%
            pct = abs((prices[-1] - prices[-2]) / prices[-2] * 100)
            if pct < 0.05:
                is_correct = True

            results.append({
                "asset": asset,
                "score": round(m["momentum_score"], 4),
                "predicted": "ALTA" if predicted_up else "QUEDA",
                "actual": "ALTA" if real_up else "QUEDA",
                "correct": is_correct,
                "change_pct": round(pct, 4),
            })

            if is_correct:
                correct += 1
            total += 1

        acc = round(correct / total * 100, 2) if total > 0 else 0

        return {
            "success": True,
            "data": {
                "total_assets": total,
                "correct": correct,
                "total": total,
                "accuracy_pct": acc,
                "assets": results,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/simulate/test-allocation")
async def test_allocation(body: dict = None):
    """Testa a alocação de capital com dados reais."""
    body = body or {}
    capital = float(body.get("capital", 150))

    try:
        all_assets = list(settings.ALL_ASSETS)
        market_data = None

        if MARKET_DATA_AVAILABLE and market_data_service:
            try:
                klines = await market_data_service.get_all_klines(all_assets, "5m", 100)
                if klines:
                    market_data = klines
            except Exception:
                pass

        if not market_data:
            market_data = test_assets_data

        momentum_results = MomentumAnalyzer.calculate_multiple_assets(market_data)
        momentum_scores = {a: d["momentum_score"] for a, d in momentum_results.items()}

        ref_asset = list(market_data.keys())[0]
        ref_data = market_data[ref_asset]
        risk_analysis = RiskAnalyzer.calculate_irq(
            ref_data.get("prices", []),
            ref_data.get("volumes", []),
        )
        irq_score = risk_analysis["irq_score"]

        allocation = PortfolioManager.calculate_portfolio_allocation(
            momentum_scores, irq_score, capital,
        )

        total_allocated = sum(v for v in allocation.values() if v > 0)

        return {
            "success": True,
            "data": {
                "irq": round(irq_score, 4),
                "total_allocated": round(total_allocated, 2),
                "allocation_pct": round(total_allocated / capital * 100, 2) if capital > 0 else 0,
                "allocations": {a: round(v, 2) for a, v in allocation.items()},
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════
# ENDPOINTS: ML / TREINAMENTO
# ═══════════════════════════════════════════

@app.get("/ml/training-data")
async def ml_get_training_data(limit: int = 2000, asset: str = None, tf: str = None):
    """
    Retorna amostras de treinamento coletadas (features + outcomes por trade).
    Cada ciclo gera N amostras (uma por ativo operado).
    Usado para treinar modelo de classificação: 'esse trade vai lucrar?'
    """
    if not DB_AVAILABLE or not db:
        raise HTTPException(status_code=503, detail="Banco de dados não disponível")
    try:
        samples = db.get_ml_training_data(limit=limit, asset=asset, tf=tf)
        stats = db.get_ml_stats()
        return {
            "success": True,
            "data": {
                "stats": stats,
                "samples": samples,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ml/train")
async def ml_train():
    """
    Treina Regressão Logística nas amostras coletadas.
    Requer: scikit-learn instalado + mínimo 200 amostras.
    Retorna: acurácia, CV score, feature importances e veredicto de edge.
    """
    if not DB_AVAILABLE or not db:
        raise HTTPException(status_code=503, detail="Banco de dados não disponível")
    try:
        samples = db.get_ml_training_data(limit=5000)
        if len(samples) < 50:
            return {
                "success": False,
                "message": f"Amostras insuficientes: {len(samples)}/50 mínimo. "
                           f"Continue rodando o bot — cada ciclo gera 2-4 amostras.",
            }

        FEATURE_COLS = [
            "momentum_score", "roc_score", "rsi_score", "trend_score",
            "volume_score", "candle_score", "signal_quality", "irq_score",
            "hour_norm", "atr_sl", "atr_tp", "vol_mult",
        ]

        X, y = [], []
        for s in samples:
            X.append([
                float(s.get("momentum_score", 0)),
                float(s.get("roc_score", 0)),
                float(s.get("rsi_score", 0)),
                float(s.get("trend_score", 0)),
                float(s.get("volume_score", 0)),
                float(s.get("candle_score", 0)),
                float(s.get("signal_quality", 0)),
                float(s.get("irq_score", 0)),
                float(s.get("hour_of_day", 0)) / 24.0,
                float(s.get("atr_sl", 0)),
                float(s.get("atr_tp", 0)),
                float(s.get("vol_mult", 1.0)),
            ])
            y.append(int(s.get("label", 0)))

        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
            from sklearn.model_selection import cross_val_score
            import numpy as _np
            import json as _json

            X_arr = _np.array(X)
            y_arr = _np.array(y)

            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X_arr)

            model = LogisticRegression(max_iter=1000, random_state=42)

            # Cross-validation (k=5 se >= 100 amostras, k=3 se < 100)
            cv_scores = []
            if len(samples) >= 30:
                k = min(5, len(samples) // 20)
                k = max(k, 3)
                cv_scores = cross_val_score(
                    model, X_scaled, y_arr, cv=k, scoring="accuracy"
                ).tolist()

            model.fit(X_scaled, y_arr)

            train_acc = float(model.score(X_scaled, y_arr))
            base_wr = sum(y) / len(y)
            cv_mean = sum(cv_scores) / len(cv_scores) if cv_scores else train_acc

            # Feature importances (coeficientes normalizados)
            coefs = {
                name: round(float(c), 4)
                for name, c in zip(FEATURE_COLS, model.coef_[0])
            }
            coefs_sorted = dict(sorted(coefs.items(), key=lambda x: abs(x[1]), reverse=True))

            # Veredicto de edge
            edge_vs_random = cv_mean - base_wr
            if edge_vs_random > 0.08:
                verdict = "EDGE FORTE — modelo supera chance aleatória em >8%"
            elif edge_vs_random > 0.04:
                verdict = "EDGE MODERADO — modelo supera aleatório em >4%"
            elif edge_vs_random > 0.01:
                verdict = "EDGE FRACO — ligeira melhora vs aleatório"
            else:
                verdict = "SEM EDGE CLARO — modelo não supera aleatorio"

            # Salvar modelo em JSON para uso futuro
            import os as _os
            model_data = {
                "trained_at": datetime.utcnow().isoformat(),
                "n_samples": len(samples),
                "feature_names": FEATURE_COLS,
                "scaler_mean": scaler.mean_.tolist(),
                "scaler_std": scaler.scale_.tolist(),
                "coefs": model.coef_[0].tolist(),
                "intercept": float(model.intercept_[0]),
                "train_accuracy": train_acc,
                "cv_accuracy_mean": cv_mean,
                "base_win_rate": base_wr,
                "edge_vs_random": round(edge_vs_random, 4),
                "verdict": verdict,
            }
            _os.makedirs("data", exist_ok=True)
            with open("data/ml_model.json", "w") as _f:
                _json.dump(model_data, _f, indent=2)

            return {
                "success": True,
                "data": {
                    "n_samples": len(samples),
                    "train_accuracy": round(train_acc, 4),
                    "cv_accuracy_mean": round(cv_mean, 4),
                    "cv_accuracy_std": round(float(_np.std(cv_scores)), 4) if cv_scores else None,
                    "base_win_rate": round(base_wr, 4),
                    "edge_vs_random_pct": round(edge_vs_random * 100, 2),
                    "verdict": verdict,
                    "feature_importance": coefs_sorted,
                    "intercept": round(float(model.intercept_[0]), 4),
                    "model_saved": "data/ml_model.json",
                    "next_step": (
                        "Edge detectado! Rode /ml/train novamente com 500+ amostras para confirmar."
                        if edge_vs_random > 0.04 else
                        "Colete mais ciclos (objetivo: 500 amostras) e retreine."
                    ),
                },
            }
        except ImportError:
            return {
                "success": False,
                "message": "scikit-learn não instalado. Adicione 'scikit-learn' ao requirements.txt e redeploye.",
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ml/stats")
async def ml_stats():
    """Resumo rápido do dataset de treinamento ML."""
    if not DB_AVAILABLE or not db:
        raise HTTPException(status_code=503, detail="Banco de dados não disponível")
    try:
        stats = db.get_ml_stats()
        needed = max(0, 500 - stats["total_samples"])
        cycles_needed = max(0, needed // 3)  # ~3 amostras por ciclo
        return {
            "success": True,
            "data": {
                **stats,
                "samples_needed_for_training": needed,
                "estimated_cycles_needed": cycles_needed,
                "estimated_days": round(cycles_needed / 144, 1),  # 144 ciclos/dia (10min)
                "status": (
                    "PRONTO PARA TREINAR"    if stats["total_samples"] >= 500 else
                    "COLETANDO (mínimo ok)"  if stats["total_samples"] >= 200 else
                    "COLETANDO"
                ),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/backtest")
async def run_backtest_endpoint(body: dict = None):
    """
    Executa backtesting walk-forward com dados históricos reais do Yahoo Finance.

    Body (opcional):
        interval:            "1d" | "1h" | "30m"  (default "1d")
        limit:               int  — quantos candles buscar (default 120)
        rebalance_interval:  int  — passos entre rebalanceamentos (default 1)
        assets:              list — lista de símbolos (default ALL_ASSETS)
    """
    from backtest import run_real_backtest

    body = body or {}
    interval           = body.get("interval", "1d")
    limit              = int(body.get("limit", 120))
    rebalance_interval = int(body.get("rebalance_interval", 1))
    assets             = body.get("assets") or list(settings.ALL_ASSETS)
    capital            = float(body.get("capital", _trade_state.get("capital", settings.INITIAL_CAPITAL)))

    if limit > 200:
        limit = 200  # Yahoo Finance free API cap

    try:
        report = await run_real_backtest(
            assets=assets,
            interval=interval,
            limit=limit,
            rebalance_interval=rebalance_interval,
            initial_capital=capital,
        )

        # Salvar resultado do backtest no performance state
        _perf_state["last_backtest"] = {
            "timestamp":       report.get("timestamp"),
            "interval":        interval,
            "limit":           limit,
            "total_return_pct":report.get("total_return_pct"),
            "avg_daily_pnl":   report.get("avg_daily_pnl"),
            "win_rate_pct":    report.get("win_rate_pct"),
            "sharpe_ratio":    report.get("sharpe_ratio"),
            "max_drawdown_pct":report.get("max_drawdown_pct"),
            "data_source":     report.get("data_source"),
        }
        db_state.save_state("performance", _perf_state)

        return {
            "success": True,
            "message": f"Backtest concluído — {report.get('total_periods', 0)} períodos",
            "data":    report,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# NOVOS MODULOS — Adicionados sem alterar nada existente
# ═══════════════════════════════════════════════════════════════════════════════

# ─── 1. COTACOES DE MOEDAS & INDICES DE MERCADO ─────────────────────────────

_forex_cache: dict = {"data": {}, "ts": 0.0}
_FOREX_TTL = 300  # 5 min

async def _fetch_forex() -> dict:
    """Busca cotacoes USD/BRL, EUR/BRL, EUR/USD + indices IBOV, SP500, Nasdaq."""
    global _forex_cache
    now = _time_module.time()
    if now - _forex_cache["ts"] < _FOREX_TTL and _forex_cache["data"]:
        return _forex_cache["data"]
    import httpx
    pairs = {
        "USD/BRL": "USDBRL=X",
        "EUR/BRL": "EURBRL=X",
        "EUR/USD": "EURUSD=X",
        "GBP/BRL": "GBPBRL=X",
        "BTC/USD": "BTC-USD",
    }
    indices = {
        "IBOVESPA": "^BVSP",
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "DOW JONES": "^DJI",
        "DOLAR INDEX": "DX-Y.NYB",
    }
    result = {"currencies": {}, "indices": {}, "updated_at": datetime.now().isoformat()}
    async with httpx.AsyncClient(timeout=10) as client:
        for label, symbol in {**pairs, **indices}.items():
            try:
                r = await client.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                    params={"interval": "1d", "range": "5d"},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if r.status_code == 200:
                    meta = r.json()["chart"]["result"][0]["meta"]
                    price = float(meta.get("regularMarketPrice", 0))
                    prev  = float(meta.get("chartPreviousClose", 0) or meta.get("previousClose", 0))
                    change_pct = round((price - prev) / prev * 100, 2) if prev else 0
                    entry = {
                        "price": round(price, 4),
                        "previous_close": round(prev, 4),
                        "change_pct": change_pct,
                        "symbol": symbol,
                    }
                    if label in pairs:
                        result["currencies"][label] = entry
                    else:
                        result["indices"][label] = entry
            except Exception:
                pass
    _forex_cache = {"data": result, "ts": now}
    return result


@app.get("/market/forex")
async def get_forex():
    """Cotacoes de moedas (USD/BRL, EUR/BRL etc) e indices de mercado (IBOV, SP500, Nasdaq)."""
    data = await _fetch_forex()
    return {"success": True, "data": data}


# ─── 2. INDICADORES TECNICOS AVANCADOS ──────────────────────────────────────

def _calc_bollinger(prices: list, period: int = 20, num_std: float = 2.0) -> dict:
    if len(prices) < period:
        return {}
    window = prices[-period:]
    sma = sum(window) / period
    variance = sum((p - sma) ** 2 for p in window) / period
    std = variance ** 0.5
    return {
        "sma": round(sma, 4),
        "upper": round(sma + num_std * std, 4),
        "lower": round(sma - num_std * std, 4),
        "bandwidth": round((num_std * 2 * std) / sma * 100, 3) if sma else 0,
        "current": round(prices[-1], 4),
        "position": round((prices[-1] - (sma - num_std * std)) / (2 * num_std * std) * 100, 1) if std else 50,
    }


def _calc_macd(prices: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    if len(prices) < slow + signal:
        return {}
    def ema_series(data, period):
        k = 2 / (period + 1)
        result = [sum(data[:period]) / period]
        for p in data[period:]:
            result.append(p * k + result[-1] * (1 - k))
        return result
    ema_fast = ema_series(prices, fast)
    ema_slow = ema_series(prices, slow)
    offset = slow - fast
    macd_line = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
    sig_line = ema_series(macd_line, signal)
    offset2 = len(macd_line) - len(sig_line)
    histogram = [macd_line[i + offset2] - sig_line[i] for i in range(len(sig_line))]
    return {
        "macd": round(macd_line[-1], 6),
        "signal": round(sig_line[-1], 6),
        "histogram": round(histogram[-1], 6),
        "trend": "ALTA" if histogram[-1] > 0 else "BAIXA",
        "crossover": "COMPRA" if len(histogram) >= 2 and histogram[-2] <= 0 < histogram[-1] else (
            "VENDA" if len(histogram) >= 2 and histogram[-2] >= 0 > histogram[-1] else "NEUTRO"
        ),
    }


def _calc_stochastic(prices: list, highs: list, lows: list, k_period: int = 14, d_period: int = 3) -> dict:
    if len(prices) < k_period + d_period:
        return {}
    k_values = []
    for i in range(k_period - 1, len(prices)):
        h = max(highs[i - k_period + 1:i + 1])
        l = min(lows[i - k_period + 1:i + 1])
        k_val = ((prices[i] - l) / (h - l) * 100) if (h - l) > 0 else 50
        k_values.append(k_val)
    d_values = [sum(k_values[i:i + d_period]) / d_period for i in range(len(k_values) - d_period + 1)]
    return {
        "k": round(k_values[-1], 2),
        "d": round(d_values[-1], 2) if d_values else 0,
        "zone": "SOBRECOMPRADO" if k_values[-1] > 80 else ("SOBREVENDIDO" if k_values[-1] < 20 else "NEUTRO"),
        "crossover": "COMPRA" if len(d_values) >= 2 and k_values[-2] <= d_values[-2] and k_values[-1] > d_values[-1] else (
            "VENDA" if len(d_values) >= 2 and k_values[-2] >= d_values[-2] and k_values[-1] < d_values[-1] else "NEUTRO"
        ),
    }


def _calc_fibonacci(prices: list) -> dict:
    if len(prices) < 10:
        return {}
    high = max(prices[-50:]) if len(prices) >= 50 else max(prices)
    low = min(prices[-50:]) if len(prices) >= 50 else min(prices)
    diff = high - low
    if diff == 0:
        return {}
    levels = {
        "0.0": round(low, 4),
        "23.6": round(low + 0.236 * diff, 4),
        "38.2": round(low + 0.382 * diff, 4),
        "50.0": round(low + 0.500 * diff, 4),
        "61.8": round(low + 0.618 * diff, 4),
        "78.6": round(low + 0.786 * diff, 4),
        "100.0": round(high, 4),
    }
    current = prices[-1]
    nearest = min(levels.values(), key=lambda x: abs(x - current))
    nearest_level = [k for k, v in levels.items() if v == nearest][0]
    return {
        "levels": levels,
        "current": round(current, 4),
        "nearest_level": nearest_level,
        "nearest_price": nearest,
        "trend": "ALTA" if current > levels["50.0"] else "BAIXA",
    }


def _calc_vwap(prices: list, volumes: list) -> dict:
    if len(prices) < 5 or len(volumes) < 5:
        return {}
    cum_pv = 0.0
    cum_vol = 0.0
    vwap_values = []
    for p, v in zip(prices, volumes):
        cum_pv += p * v
        cum_vol += v
        vwap_values.append(cum_pv / cum_vol if cum_vol else p)
    vwap = vwap_values[-1]
    current = prices[-1]
    return {
        "vwap": round(vwap, 4),
        "current": round(current, 4),
        "deviation_pct": round((current - vwap) / vwap * 100, 3) if vwap else 0,
        "position": "ACIMA" if current > vwap else "ABAIXO",
        "signal": "COMPRA" if current < vwap * 0.99 else ("VENDA" if current > vwap * 1.01 else "NEUTRO"),
    }


def _rsi_calc(prices: list, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(c, 0) for c in changes]
    losses = [abs(min(c, 0)) for c in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


@app.get("/market/indicators/{asset}")
async def get_technical_indicators(asset: str, interval: str = "5m", limit: int = 100):
    """Indicadores tecnicos completos: MACD, Bollinger, Stochastic, Fibonacci, VWAP, RSI."""
    if not MARKET_DATA_AVAILABLE:
        raise HTTPException(status_code=503, detail="Servico de dados nao disponivel")
    try:
        klines = await market_data_service.get_klines(asset.upper(), interval, limit)
        if not klines or klines.get("count", 0) < 10:
            raise HTTPException(status_code=404, detail=f"Dados insuficientes para {asset}")
        prices  = klines["prices"]
        volumes = klines["volumes"]
        highs   = klines.get("highs", prices)
        lows    = klines.get("lows", prices)
        return {
            "success": True,
            "asset": asset.upper(),
            "interval": interval,
            "candles": len(prices),
            "data": {
                "rsi": _rsi_calc(prices),
                "macd": _calc_macd(prices),
                "bollinger": _calc_bollinger(prices),
                "stochastic": _calc_stochastic(prices, highs, lows),
                "fibonacci": _calc_fibonacci(prices),
                "vwap": _calc_vwap(prices, volumes),
                "summary": {
                    "current_price": round(prices[-1], 4),
                    "high": round(max(prices[-20:]), 4) if len(prices) >= 20 else round(max(prices), 4),
                    "low": round(min(prices[-20:]), 4) if len(prices) >= 20 else round(min(prices), 4),
                    "volatility_pct": round(
                        (max(prices[-20:]) - min(prices[-20:])) / min(prices[-20:]) * 100, 2
                    ) if len(prices) >= 20 and min(prices[-20:]) > 0 else 0,
                },
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/market/indicators-all")
async def get_all_indicators(interval: str = "5m"):
    """Indicadores tecnicos resumidos para TODOS os ativos."""
    if not MARKET_DATA_AVAILABLE:
        raise HTTPException(status_code=503, detail="Servico de dados nao disponivel")
    try:
        assets = list(settings.ALL_ASSETS)
        klines_data = await market_data_service.get_all_klines(assets, interval, 100)
        result = {}
        for asset in assets:
            d = klines_data.get(asset, {})
            prices  = d.get("prices", [])
            if len(prices) < 10:
                continue
            rsi_val = _rsi_calc(prices)
            macd = _calc_macd(prices)
            boll = _calc_bollinger(prices)
            signals = 0
            if rsi_val < 30: signals += 1
            elif rsi_val > 70: signals -= 1
            if macd.get("crossover") == "COMPRA": signals += 1
            elif macd.get("crossover") == "VENDA": signals -= 1
            if boll.get("position", 50) < 20: signals += 1
            elif boll.get("position", 50) > 80: signals -= 1
            consensus = "COMPRA" if signals >= 2 else ("VENDA" if signals <= -2 else "NEUTRO")
            result[asset] = {
                "price": round(prices[-1], 4),
                "rsi": rsi_val,
                "macd_trend": macd.get("trend", "--"),
                "macd_cross": macd.get("crossover", "--"),
                "boll_position": boll.get("position", 50),
                "boll_band": f"{boll.get('lower', 0):.2f} - {boll.get('upper', 0):.2f}" if boll else "--",
                "consensus": consensus,
            }
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── 3. MOTOR DE CALCULO FINANCEIRO (Taxas, Impostos, Preco Medio) ──────────

_FEE_RATES = {
    "b3_corretagem": 0.0,
    "b3_emolumentos": 0.00005,
    "b3_liquidacao": 0.000275,
    "crypto_maker": 0.001,
    "crypto_taker": 0.001,
    "us_corretagem": 0.0,
}

_TAX_RATES = {
    "br_daytrade": 0.20,
    "br_swing": 0.15,
    "br_crypto": 0.15,
    "br_isencao_swing_mensal": 20000.0,
    "br_isencao_crypto_mensal": 35000.0,
}


@app.get("/finance/calculator")
async def finance_calculator():
    """Motor de calculo financeiro: preco medio, taxas, impostos, rentabilidade."""
    td = _trade_state
    capital = td.get("capital", settings.INITIAL_CAPITAL)
    total_pnl = td.get("total_pnl", 0)
    positions = td.get("positions", {})

    portfolio_detail = []
    total_allocated = 0.0
    total_fees_estimated = 0.0
    total_tax_estimated = 0.0

    crypto_set = {c.upper() for c in settings.CRYPTO_ASSETS}
    b3_set = {a.upper() for a in settings.ALLOWED_ASSETS}

    for asset, pos in positions.items():
        qty = pos.get("quantity", 0)
        entry = pos.get("entry_price", 0)
        pnl = pos.get("pnl", 0)
        alloc = pos.get("allocated", 0)
        if qty <= 0 and alloc <= 0:
            continue

        is_crypto = asset.upper() in crypto_set or "USDT" in asset.upper()
        is_b3 = asset.upper() in b3_set

        if is_crypto:
            fee_rate = _FEE_RATES["crypto_taker"]
            tax_rate = _TAX_RATES["br_crypto"]
        elif is_b3:
            fee_rate = _FEE_RATES["b3_emolumentos"] + _FEE_RATES["b3_liquidacao"]
            tax_rate = _TAX_RATES["br_daytrade"]
        else:
            fee_rate = 0.0
            tax_rate = _TAX_RATES["br_swing"]

        value = alloc if alloc else qty * entry
        fees = value * fee_rate * 2
        tax = max(0, pnl * tax_rate) if pnl > 0 else 0
        net_pnl = pnl - fees - tax
        rentab = round(net_pnl / value * 100, 2) if value > 0 else 0

        total_allocated += value
        total_fees_estimated += fees
        total_tax_estimated += tax

        portfolio_detail.append({
            "asset": asset,
            "quantity": round(qty, 8),
            "entry_price": round(entry, 4),
            "allocated": round(value, 2),
            "pnl_bruto": round(pnl, 2),
            "fees_estimated": round(fees, 4),
            "tax_estimated": round(tax, 2),
            "pnl_liquido": round(net_pnl, 2),
            "rentabilidade_pct": rentab,
            "asset_type": "crypto" if is_crypto else ("b3" if is_b3 else "us"),
        })

    gross_pnl = total_pnl
    net_pnl = gross_pnl - total_fees_estimated - total_tax_estimated
    return {
        "success": True,
        "data": {
            "summary": {
                "capital": round(capital, 2),
                "total_allocated": round(total_allocated, 2),
                "free_capital": round(capital - total_allocated, 2),
                "pnl_bruto": round(gross_pnl, 2),
                "total_fees": round(total_fees_estimated, 4),
                "total_tax_estimated": round(total_tax_estimated, 2),
                "pnl_liquido": round(net_pnl, 2),
                "rentabilidade_bruta_pct": round(gross_pnl / capital * 100, 2) if capital > 0 else 0,
                "rentabilidade_liquida_pct": round(net_pnl / capital * 100, 2) if capital > 0 else 0,
            },
            "fee_rates": _FEE_RATES,
            "tax_rates": {k: v for k, v in _TAX_RATES.items() if not k.startswith("br_isencao")},
            "tax_exemptions": {
                "swing_monthly_limit": _TAX_RATES["br_isencao_swing_mensal"],
                "crypto_monthly_limit": _TAX_RATES["br_isencao_crypto_mensal"],
                "note": "Day trade: sem isencao. Swing: isento abaixo de R$20k vendas/mes. Crypto: isento abaixo de R$35k vendas/mes.",
            },
            "positions": portfolio_detail,
        },
    }


# ─── 4. NOTICIAS, CALENDARIO DE DIVIDENDOS & EVENTOS FINANCEIROS ────────────

def _fetch_economic_calendar() -> list:
    """Calendario economico simplificado com eventos recorrentes importantes."""
    return [
        {"event": "FOMC Decision", "region": "US", "impact": "alto", "frequency": "6 semanas"},
        {"event": "Non-Farm Payrolls (NFP)", "region": "US", "impact": "alto", "frequency": "mensal (1a sexta)"},
        {"event": "CPI (Inflacao EUA)", "region": "US", "impact": "alto", "frequency": "mensal"},
        {"event": "Ata do Copom", "region": "BR", "impact": "alto", "frequency": "a cada 45 dias"},
        {"event": "IPCA (Inflacao BR)", "region": "BR", "impact": "alto", "frequency": "mensal"},
        {"event": "PIB Brasil", "region": "BR", "impact": "medio", "frequency": "trimestral"},
        {"event": "Earnings Season", "region": "US/BR", "impact": "alto", "frequency": "trimestral"},
        {"event": "Payroll (CAGED)", "region": "BR", "impact": "medio", "frequency": "mensal"},
        {"event": "PCE (Deflator EUA)", "region": "US", "impact": "alto", "frequency": "mensal"},
        {"event": "Decisao Selic (Copom)", "region": "BR", "impact": "alto", "frequency": "a cada 45 dias"},
    ]


async def _fetch_dividends(assets: list) -> dict:
    """Busca dividend yield via Yahoo Finance."""
    import httpx
    dividends = {}
    async with httpx.AsyncClient(timeout=8) as client:
        for asset in assets[:30]:
            ticker = asset
            if asset in settings.ALLOWED_ASSETS:
                ticker = f"{asset}.SA"
            try:
                r = await client.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                    params={"interval": "1d", "range": "1y"},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if r.status_code == 200:
                    meta = r.json()["chart"]["result"][0]["meta"]
                    div_yield = meta.get("trailingAnnualDividendYield")
                    div_rate  = meta.get("trailingAnnualDividendRate")
                    if div_yield is not None or div_rate is not None:
                        dividends[asset] = {
                            "dividend_yield_pct": round(float(div_yield or 0) * 100, 2),
                            "annual_dividend": round(float(div_rate or 0), 4),
                            "price": round(float(meta.get("regularMarketPrice", 0)), 4),
                        }
            except Exception:
                pass
    return dividends


@app.get("/market/events")
async def get_market_events():
    """Calendario economico, noticias RSS e eventos do mercado."""
    global _news_cache
    now = _time_module.time()
    if now - _news_cache["ts"] > _NEWS_TTL:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, _fetch_news_raw)
        sentiment_map = {a: _score_news(a, raw) for a in settings.ALL_ASSETS}
        _news_cache = {"data": sentiment_map, "ts": now}

    raw_news = []
    try:
        raw = await asyncio.get_event_loop().run_in_executor(None, _fetch_news_raw)
        for title, source in raw[:30]:
            raw_news.append({"title": title, "source": source})
    except Exception:
        pass

    calendar = _fetch_economic_calendar()
    return {
        "success": True,
        "data": {
            "news": raw_news,
            "economic_calendar": calendar,
            "sentiment_by_asset": _news_cache.get("data", {}),
            "updated_at": datetime.now().isoformat(),
        },
    }


@app.get("/market/dividends")
async def get_dividends():
    """Dividendos (dividend yield) dos ativos B3 + US."""
    try:
        b3_assets = list(settings.ALLOWED_ASSETS)
        us_top = settings.US_STOCKS[:20]
        divs = await _fetch_dividends(b3_assets + us_top)
        ranked = dict(sorted(divs.items(), key=lambda x: x[1].get("dividend_yield_pct", 0), reverse=True))
        return {"success": True, "data": ranked, "count": len(ranked)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── 5. SEGURANCA & COMPLIANCE ──────────────────────────────────────────────

_audit_log: list = []
_AUDIT_FILE = Path(__file__).resolve().parent.parent / "data" / "audit_log.json"
_MAX_AUDIT = 5000

def _load_audit():
    global _audit_log
    try:
        if _AUDIT_FILE.exists():
            _audit_log = json.loads(_AUDIT_FILE.read_text(encoding="utf-8"))
    except Exception:
        _audit_log = []

def _append_audit(action: str, details: dict = None, severity: str = "info"):
    entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "severity": severity,
        "details": details or {},
    }
    _audit_log.append(entry)
    if len(_audit_log) > _MAX_AUDIT:
        _audit_log[:] = _audit_log[-_MAX_AUDIT:]
    try:
        _AUDIT_FILE.write_text(json.dumps(_audit_log, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass

_load_audit()


@app.get("/security/audit")
async def get_audit_log(limit: int = 100, severity: str = None):
    """Audit trail de acoes do sistema (compliance)."""
    entries = _audit_log[-limit:]
    if severity:
        entries = [e for e in entries if e.get("severity") == severity]
    return {"success": True, "data": list(reversed(entries)), "total": len(_audit_log)}


@app.get("/security/status")
async def get_security_status():
    """Status de seguranca: API keys, rate limits, protecoes ativas."""
    try:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        recent_critical = sum(1 for e in _audit_log if e.get("severity") == "critical" and e.get("timestamp", "") > cutoff)
    except Exception:
        recent_critical = 0

    try:
        risk_data = risk_manager.to_dict() if risk_manager else {}
    except Exception:
        risk_data = {}

    return {
        "success": True,
        "data": {
            "api_keys": {
                "brapi_configured": bool(settings.BRAPI_TOKEN),
                "binance_configured": bool(settings.BINANCE_API_KEY),
                "btg_configured": bool(getattr(settings, "BTG_API_KEY", "")),
                "alpha_vantage_configured": bool(getattr(settings, "ALPHA_VANTAGE_KEY", "")),
                "telegram_configured": bool(settings.TELEGRAM_BOT_TOKEN),
            },
            "trading_mode": getattr(settings, "TRADING_MODE", "paper"),
            "brokers": market_data_service.broker_status() if market_data_service else {},
            "protections": {
                "stop_loss": {"active": True, "value": f"{settings.STOP_LOSS_PERCENTAGE*100}%"},
                "take_profit": {"active": True, "value": f"{settings.TAKE_PROFIT_PERCENTAGE*100}%"},
                "max_daily_loss": {"active": True, "value": f"{settings.MAX_DAILY_LOSS_PERCENTAGE*100}%"},
                "max_trades_hour": {"active": True, "value": settings.MAX_TRADES_PER_HOUR},
                "max_trades_day": {"active": True, "value": settings.MAX_TRADES_PER_DAY},
                "irq_protection": {"active": True, "thresholds": {
                    "moderate": settings.IRQ_THRESHOLD_HIGH,
                    "high": settings.IRQ_THRESHOLD_VERY_HIGH,
                    "critical": settings.IRQ_THRESHOLD_CRITICAL,
                }},
            },
            "audit": {
                "total_events": len(_audit_log),
                "critical_24h": recent_critical,
                "last_event": _audit_log[-1] if _audit_log else None,
            },
            "compliance_notes": [
                "Todas as operacoes registradas no audit log",
                "Stop loss e take profit automaticos",
                "Limites diarios de perda e operacoes",
                "IRQ com protecao em 3 niveis",
                "Dados de fontes oficiais (B3/Binance/Yahoo)",
            ],
            "risk_score": risk_data,
        },
    }


@app.middleware("http")
async def audit_middleware(request, call_next):
    """Registra acoes criticas no audit log."""
    response = await call_next(request)
    path = request.url.path
    method = request.method
    if method == "POST" and path in ["/trade/start", "/trade/stop", "/trade/capital",
                                       "/trade/cycle", "/trade/reset",
                                       "/scheduler/start", "/scheduler/stop",
                                       "/brokers/connect", "/brokers/order"]:
        _append_audit(
            f"{method} {path}",
            {"status_code": response.status_code},
            severity="warning" if "reset" in path or "order" in path else "info"
        )
    return response


# ─── 7. BROKERS — Corretoras e Exchanges ────────────────────────────────────

@app.get("/brokers/status", tags=["Brokers"])
async def brokers_status():
    """Status de todos os brokers/fontes de dados integrados."""
    if not market_data_service:
        return {"success": False, "error": "Market data service não disponível"}

    return {
        "success": True,
        "data": market_data_service.broker_status(),
    }


@app.post("/brokers/connect", tags=["Brokers"])
async def brokers_connect():
    """Tenta conectar todos os brokers configurados."""
    if not market_data_service:
        return {"success": False, "error": "Market data service não disponível"}

    results = await market_data_service.connect_brokers()
    return {
        "success": True,
        "data": results,
        "connected": [k for k, v in results.items() if v],
        "failed": [k for k, v in results.items() if not v],
    }


@app.get("/brokers/balance", tags=["Brokers"])
async def brokers_balance():
    """Saldo de todos os brokers (paper ou live)."""
    if not market_data_service:
        return {"success": False, "error": "Market data service não disponível"}

    balances = await market_data_service.get_broker_balance()
    return {
        "success": True,
        "data": balances,
    }


@app.get("/brokers/positions", tags=["Brokers"])
async def brokers_positions():
    """Posições abertas em todos os brokers."""
    if not market_data_service:
        return {"success": False, "error": "Market data service não disponível"}

    positions = await market_data_service.get_broker_positions()
    return {
        "success": True,
        "data": positions,
    }


@app.post("/brokers/order", tags=["Brokers"])
async def brokers_place_order(body: dict):
    """
    Envia ordem ao broker correto (BTG para B3, Binance para Crypto).
    Body: {"asset": "BTC", "side": "buy", "quantity": 0.001, "price": null, "type": "market"}
    Em modo paper: simula execução com preços reais.
    """
    if not market_data_service:
        return {"success": False, "error": "Market data service não disponível"}

    asset = body.get("asset", "").upper()
    side = body.get("side", "buy")
    quantity = float(body.get("quantity", 0))
    price = body.get("price")
    order_type = body.get("type", "market")

    if not asset or quantity <= 0:
        raise HTTPException(status_code=400, detail="asset e quantity são obrigatórios (quantity > 0)")

    if price is not None:
        price = float(price)

    _append_audit(
        f"ORDER {side.upper()} {quantity} {asset}",
        {"price": price, "type": order_type, "mode": getattr(settings, "TRADING_MODE", "paper")},
        severity="warning",
    )

    result = await market_data_service.place_order(asset, side, quantity, price, order_type)
    if result:
        _trade_log(f"ORDER_{side.upper()}", asset, quantity, f"{order_type} {side} {quantity} {asset} @ {price or 'market'}")
        return {"success": True, "data": result}
    else:
        return {"success": False, "error": f"Falha ao executar ordem {side} {asset}"}


@app.get("/brokers/orderbook/{asset}", tags=["Brokers"])
async def get_orderbook(asset: str, limit: int = 20):
    """Order book (livro de ofertas) de um ativo crypto."""
    if not market_data_service or not market_data_service.binance_broker:
        return {"success": False, "error": "Binance broker não disponível"}

    book = await market_data_service.binance_broker.get_orderbook(asset, limit)
    if book:
        return {"success": True, "data": book}
    return {"success": False, "error": f"Orderbook não disponível para {asset}"}


@app.get("/brokers/trades/{asset}", tags=["Brokers"])
async def get_recent_trades(asset: str, limit: int = 50):
    """Trades recentes de um ativo crypto."""
    if not market_data_service or not market_data_service.binance_broker:
        return {"success": False, "error": "Binance broker não disponível"}

    trades = await market_data_service.binance_broker.get_recent_trades(asset, limit)
    return {"success": True, "data": trades, "count": len(trades)}


@app.get("/brokers/orders", tags=["Brokers"])
async def brokers_order_history(limit: int = 50):
    """Histórico de ordens paper/live de todos os brokers."""
    if not market_data_service:
        return {"success": False, "error": "Market data service não disponível"}

    orders = []
    # Binance paper orders
    if market_data_service.binance_broker:
        bn_orders = await market_data_service.binance_broker.get_order_history(limit=limit)
        for o in bn_orders:
            o["broker"] = "binance"
        orders.extend(bn_orders)
    # BTG paper orders
    if market_data_service.btg_broker:
        btg_orders = await market_data_service.btg_broker.get_order_history(limit=limit)
        for o in btg_orders:
            o["broker"] = "btg"
        orders.extend(btg_orders)

    # Ordenar por timestamp (mais recente primeiro)
    orders.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return {
        "success": True,
        "data": orders[:limit],
        "total": len(orders),
    }


# ─── 8. DADOS CONSOLIDADOS DO USUARIO ───────────────────────────────────────

@app.get("/user/summary")
async def get_user_summary():
    """Resumo consolidado: saldo, carteira, P&L, historico, risco."""
    td = _trade_state
    perf = _perf_state
    capital = td.get("capital", settings.INITIAL_CAPITAL)
    total_pnl = td.get("total_pnl", 0)
    positions = td.get("positions", {})
    log = td.get("log", [])

    active_positions = {k: v for k, v in positions.items()
                        if v.get("quantity", 0) > 0 or v.get("allocated", 0) > 0}
    total_allocated = sum(p.get("allocated", 0) for p in active_positions.values())

    cycles = perf.get("cycles", [])
    wins = perf.get("win_count", 0)
    losses = perf.get("loss_count", 0)
    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    pnl_5m = sum(c.get("pnl_5m", 0) for c in cycles)
    pnl_1h = sum(c.get("pnl_1h", 0) for c in cycles)
    pnl_1d = sum(c.get("pnl_1d", 0) for c in cycles)

    return {
        "success": True,
        "data": {
            "account": {
                "capital": round(capital, 2),
                "total_pnl": round(total_pnl, 2),
                "patrimonio_total": round(capital + total_pnl, 2),
                "free_capital": round(capital - total_allocated, 2),
                "allocated": round(total_allocated, 2),
                "rentabilidade_pct": round(total_pnl / capital * 100, 2) if capital > 0 else 0,
            },
            "portfolio": {
                "active_count": len(active_positions),
                "positions": {k: {
                    "allocated": round(v.get("allocated", 0), 2),
                    "pnl": round(v.get("pnl", 0), 4),
                    "entry_price": round(v.get("entry_price", 0), 4),
                    "timeframe": v.get("timeframe", "--"),
                } for k, v in active_positions.items()},
            },
            "performance": {
                "total_cycles": _effective_total_cycles(),
                "win_count": wins,
                "loss_count": losses,
                "win_rate_pct": round(win_rate, 1),
                "pnl_by_timeframe": {"5m": round(pnl_5m, 2), "1h": round(pnl_1h, 2), "1d": round(pnl_1d, 2)},
                "best_cycle": round(perf.get("best_day_pnl", 0), 2),
                "worst_cycle": round(perf.get("worst_day_pnl", 0), 2),
            },
            "risk": risk_manager.to_dict() if risk_manager else {},
            "recent_activity": log[-20:] if log else [],
            "updated_at": datetime.now().isoformat(),
        },
    }


from fastapi import Request
from fastapi import HTTPException

@app.post("/admin/set-capital")
async def set_capital(current_capital: float):
    """Endpoint para alterar o capital diretamente."""
    if current_capital <= 0:
        raise HTTPException(status_code=400, detail="Capital deve ser maior que zero.")

    _trade_state["capital"] = current_capital
    db_state.save_state("trade_state", _trade_state)

    return {"success": True, "new_capital": _trade_state["capital"]}

if __name__ == "__main__":
    import uvicorn
    import os

    port = int(os.getenv("PORT", 8001))
    # debug desligado automaticamente em produção (Railway)
    _debug = settings.DEBUG and not os.getenv("RAILWAY_ENVIRONMENT")
    uvicorn.run(app, host="0.0.0.0", port=port, debug=_debug)
