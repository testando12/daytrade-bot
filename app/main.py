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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from datetime import datetime
from pathlib import Path
import asyncio
import json

from app.core.config import settings
from app.engines import MomentumAnalyzer, RiskAnalyzer, PortfolioManager
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
# SCHEDULER AUTOMÁTICO
# ═══════════════════════════════════════════

_scheduler_state = {
    "running": False,
    "interval_minutes": 30,   # ciclo a cada 30 minutos (B3); mín 60min fora do B3
    "only_market_hours": True, # mantido por compatibilidade; crypto sempre roda
    "next_run": None,
    "total_auto_cycles": 0,
    "task": None,
    "session": "",             # sessão atual: "B3+Crypto" ou "Crypto24/7"
}

# Controle de reinvestimento diário
_last_reinvestment_date: str = ""  # data da última vez que reinvestiu (YYYY-MM-DD)

# Alocação por timeframe: SHORT 20%, MEDIUM 35%, LONG 45%
_TIMEFRAME_ALLOC = {"5m": 0.20, "1h": 0.35, "1d": 0.45}
_TIMEFRAME_N_ASSETS = {"5m": 5, "1h": 7, "1d": 9}  # top N ativos por bucket

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
    - B3 aberta (seg-sex 10-17h)       → B3 + US + Crypto (todos os ativos)
    - NYSE aberta fora B3 (13h30-20h)  → US Stocks + Crypto
    - Fora de horário                  → somente Crypto 24/7
    """
    from datetime import timezone, timedelta
    brt = timezone(timedelta(hours=-3))
    now = datetime.now(brt)
    weekday = now.weekday()  # 0=seg .. 4=sex
    hour    = now.hour
    minute  = now.minute

    b3_open  = weekday < 5 and 10 <= hour < 17
    # NYSE abre 9h30 EST = 13h30 BRT (-3h do fuso de NY em horário de verão dos EUA)
    nyse_open = weekday < 5 and (hour > 13 or (hour == 13 and minute >= 30)) and hour < 20

    if b3_open:
        total = len(settings.ALL_ASSETS)
        return settings.ALL_ASSETS, f"[BR] B3 + [US] US + [CRYPTO] ({total} ativos)"
    elif nyse_open:
        us_crypto = settings.US_STOCKS + settings.CRYPTO_ASSETS
        return us_crypto, f"[US] NYSE + [CRYPTO] ({len(us_crypto)} ativos)"
    else:
        return settings.CRYPTO_ASSETS, f"[CRYPTO] 24/7 ({len(settings.CRYPTO_ASSETS)} ativos)"


async def _auto_cycle_loop():
    """Loop interno do scheduler: executa ciclos de trading automaticamente."""
    global _last_reinvestment_date
    _scheduler_state["running"] = True
    print("[scheduler] Iniciado - intervalo:", _scheduler_state["interval_minutes"], "min", flush=True)
    # Aguarda o servidor subir completamente antes do primeiro ciclo
    await asyncio.sleep(20)
    while _scheduler_state["running"]:
        from datetime import timezone, timedelta
        brt = timezone(timedelta(hours=-3))
        now_brt = datetime.now(brt)
        today_str = now_brt.strftime("%Y-%m-%d")

        # ✨ Reinvestimento automático: após 17h BRT, reinveste lucro do dia
        if now_brt.weekday() < 5 and now_brt.hour == 17 and _last_reinvestment_date != today_str:
            try:
                today_cycles = [c for c in _perf_state.get("cycles", []) if c.get("timestamp", "").startswith(today_str)]
                today_pnl = round(sum(c.get("pnl", 0) for c in today_cycles), 2)
                if today_pnl != 0:
                    _trade_state["capital"] = round(_trade_state["capital"] + today_pnl, 2)
                    sinal = "⬆ Lucro" if today_pnl > 0 else "⬇ Prejuízo"
                    _trade_log("REINVESTIMENTO", "—", today_pnl,
                        f"💰 {sinal} do dia R$ {today_pnl:+.2f} reinvestido. Novo capital: R$ {_trade_state['capital']:.2f}")
                    print(f"[scheduler] Reinvestimento: R$ {today_pnl:+.2f} -> capital agora R$ {_trade_state['capital']:.2f}", flush=True)
                _last_reinvestment_date = today_str
            except Exception as e:
                print(f"[scheduler] Erro no reinvestimento: {e}", flush=True)

        interval_sec = _scheduler_state["interval_minutes"] * 60
        _scheduler_state["next_run"] = datetime.now().isoformat()

        # Determina sessão: B3+Crypto ou Crypto-only
        active_assets, session_label = _current_session()
        _scheduler_state["session"] = session_label

        # Intervalo maior fora do horário B3 (crypto é menos volátil à noite)
        if _is_market_open():
            interval_sec = _scheduler_state["interval_minutes"] * 60
        else:
            interval_sec = max(_scheduler_state["interval_minutes"], 60) * 60  # mín 1h fora do B3

        try:
            result = await _run_trade_cycle_internal(assets=active_assets)
            _scheduler_state["total_auto_cycles"] += 1
            pnl = result.get("cycle_pnl", 0)
            irq = result.get("irq", 0)
            print(
                f"[scheduler] Ciclo #{_scheduler_state['total_auto_cycles']} [{session_label}] "
                f"| P&L: R$ {pnl:.4f} (5m:{result.get('pnl_5m',0):.2f} 1h:{result.get('pnl_1h',0):.2f} 1d:{result.get('pnl_1d',0):.2f})"
                f" | IRQ: {irq:.3f}",
                flush=True,
            )
        except Exception as e:
            print(f"[scheduler] Erro no ciclo automático: {e}", flush=True)

        await asyncio.sleep(interval_sec)

    print("[scheduler] Parado.", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicia o scheduler automático quando o servidor sobe."""
    task = asyncio.create_task(_auto_cycle_loop())
    _scheduler_state["task"] = task
    yield
    # Shutdown
    _scheduler_state["running"] = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# Criar aplicação
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Bot de Day Trade Automatizado com Análise de Momentum e Risco",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.get("/health")
async def health_check():
    """Health check"""
    return {"status": "ok", "timestamp": datetime.utcnow()}


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
        allocation = PortfolioManager.calculate_portfolio_allocation(
            momentum_scores,
            risk_analysis["irq_score"],
            settings.INITIAL_CAPITAL,
        )
        risk_metrics = PortfolioManager.calculate_risk_metrics(allocation, settings.INITIAL_CAPITAL)

        status = {
            "is_running": True,
            "last_analysis": datetime.utcnow(),
            "total_capital": settings.INITIAL_CAPITAL,
            "current_balance": settings.INITIAL_CAPITAL,
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


@app.post("/alerts/setup-telegram")
async def setup_telegram_alerts(bot_token: str, chat_id: str):
    """Configura alertas Telegram"""
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


@app.post("/alerts/setup-discord")
async def setup_discord_alerts(webhook_url: str):
    """Configura alertas Discord"""
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
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

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
    "auto_trading": False,
    "positions": {},       # asset -> {"amount": float, "action": str, "pct": float}
    "log": [],             # lista de eventos {timestamp, type, asset, amount, note}
    "total_pnl": 0.0,
    "last_cycle": None,
}
_trade_state: dict = _load_json(_STATE_FILE, dict(_DEFAULT_TRADE_STATE))
# Corrige valores corrompidos do bug antigo de P&L
if _trade_state.get("total_pnl", 0.0) < -5:
    _trade_state["total_pnl"] = 0.0
    _save_json(_STATE_FILE, _trade_state)
# Se o capital salvo for menor que o capital configurado, atualiza para o maior
if _trade_state.get("capital", 0) < settings.INITIAL_CAPITAL:
    _trade_state["capital"] = settings.INITIAL_CAPITAL
    _save_json(_STATE_FILE, _trade_state)

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
}
_perf_state: dict = _load_json(_PERF_FILE, dict(_DEFAULT_PERF))


def _trade_log(event_type: str, asset: str, amount: float, note: str):
    """Insere um evento no log de trading (máx 200 entradas) e persiste em disco."""
    _trade_state["log"].insert(0, {
        "timestamp": datetime.now().isoformat(),
        "type": event_type,
        "asset": asset,
        "amount": round(amount, 2),
        "note": note,
    })
    if len(_trade_state["log"]) > 200:
        _trade_state["log"] = _trade_state["log"][:200]
    _save_json(_STATE_FILE, _trade_state)


def _record_cycle_performance(pnl: float, capital: float, irq: float,
                               pnl_5m: float = 0.0, pnl_1h: float = 0.0, pnl_1d: float = 0.0):
    """Registra o P&L de um ciclo no histórico de performance."""
    _perf_state["cycles"].append({
        "timestamp": datetime.now().isoformat(),
        "pnl":       round(pnl, 4),
        "pnl_5m":    round(pnl_5m, 4),
        "pnl_1h":    round(pnl_1h, 4),
        "pnl_1d":    round(pnl_1d, 4),
        "capital":   round(capital, 2),
        "irq":       round(irq, 4),
    })
    # manter máx 500 ciclos
    if len(_perf_state["cycles"]) > 500:
        _perf_state["cycles"] = _perf_state["cycles"][-500:]

    _perf_state["total_pnl_history"].append(round(capital, 2))
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

    _save_json(_PERF_FILE, _perf_state)


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
    capital_base = _trade_state["capital"]
    capital_efetivo = round(capital_base + pnl_today_live, 2)
    return {
        "success": True,
        "data": {
            "capital":          capital_base,
            "capital_efetivo":  capital_efetivo,
            "pnl_hoje":         pnl_today_live,
            "auto_trading":     _trade_state["auto_trading"],
            "total_pnl":        _trade_state["total_pnl"],
            "positions":        _trade_state["positions"],
            "log":              _trade_state["log"],
            "last_cycle":       _trade_state["last_cycle"],
            "b3_open":          _is_market_open(),
            "session":          session_label,
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
    _trade_log(event, "—", abs(delta), f"Capital {event.lower()} de R$ {prev:.2f} → R$ {amount:.2f}")
    return {"success": True, "capital": amount, "previous": prev}


@app.post("/trade/start")
async def start_auto_trading():
    """Ativa o trading automático."""
    _trade_state["auto_trading"] = True
    _trade_log("SISTEMA", "—", 0, "✅ Trading automático INICIADO")
    return {"success": True, "auto_trading": True}


@app.post("/trade/stop")
async def stop_auto_trading():
    """Pausa o trading automático."""
    _trade_state["auto_trading"] = False
    _trade_log("SISTEMA", "—", 0, "⏸ Trading automático PAUSADO")
    return {"success": True, "auto_trading": False}


@app.get("/trade/reset")
@app.post("/trade/reset")
async def reset_trade_state():
    """Zera o histórico de P&L, ciclos e restaura capital ao valor padrão."""
    _trade_state["total_pnl"] = 0.0
    _trade_state["capital"]   = settings.INITIAL_CAPITAL
    _trade_state["log"]       = []
    _trade_state["positions"] = {}
    _save_json(_STATE_FILE, _trade_state)
    _perf_state["cycles"] = []
    _perf_state["total_pnl_history"] = []
    _perf_state["win_count"] = 0
    _perf_state["loss_count"] = 0
    _perf_state["best_day_pnl"] = 0.0
    _perf_state["worst_day_pnl"] = 0.0
    _save_json(_PERF_FILE, _perf_state)
    return {"success": True, "message": f"Histórico zerado — capital restaurado para R$ {settings.INITIAL_CAPITAL:.2f}"}


@app.post("/trade/cycle")
async def run_trade_cycle():
    """
    Executa um ciclo de análise e simula as ordens que o bot colocaria.
    Registra cada decisão no log de trading.
    """
    result = await _run_trade_cycle_internal()
    return {
        "success": True,
        "data": result,
    }


async def _run_trade_cycle_internal(assets: list = None) -> dict:
    """Lógica interna de um ciclo de trading com alocação em 3 timeframes (20/35/45%)."""
    capital = _trade_state["capital"]
    all_assets = assets if assets is not None else settings.ALL_ASSETS
    b3_open = _is_market_open()
    session_label = "B3+Crypto" if (assets is None or len(all_assets) > len(settings.CRYPTO_ASSETS)) else "Crypto24/7"
    data_source = "test"

    # ── 1. Buscar dados para os 3 timeframes ─────────────────────────────
    klines_by_tf: dict = {"5m": None, "1h": None, "1d": None}
    if MARKET_DATA_AVAILABLE and market_data_service:
        for tf in ("5m", "1h", "1d"):
            try:
                k = await market_data_service.get_all_klines(all_assets, tf, 25)
                if k:
                    klines_by_tf[tf] = k
                    data_source = "brapi/yahoo"
            except Exception:
                pass
    # Fallback para dados de teste
    for tf in ("5m", "1h", "1d"):
        if not klines_by_tf[tf]:
            klines_by_tf[tf] = test_assets_data

    # ── 2. Top N ativos por momentum em cada timeframe ───────────────────
    def _top_assets(klines, n):
        mom = MomentumAnalyzer.calculate_multiple_assets(klines)
        sorted_assets = sorted(mom.items(), key=lambda x: x[1].get("momentum_score", 0), reverse=True)
        return [a for a, _ in sorted_assets[:n]], mom

    capital_5m = round(capital * _TIMEFRAME_ALLOC["5m"], 2)
    capital_1h = round(capital * _TIMEFRAME_ALLOC["1h"], 2)
    capital_1d = round(capital * _TIMEFRAME_ALLOC["1d"], 2)

    top_5m, mom_5m = _top_assets(klines_by_tf["5m"], _TIMEFRAME_N_ASSETS["5m"])
    top_1h, mom_1h = _top_assets(klines_by_tf["1h"], _TIMEFRAME_N_ASSETS["1h"])
    top_1d, mom_1d = _top_assets(klines_by_tf["1d"], _TIMEFRAME_N_ASSETS["1d"])

    # ── 3. Risco (referência via 5m) ─────────────────────────────────────
    ref_asset = top_5m[0] if top_5m else list(klines_by_tf["5m"].keys())[0]
    ref_data  = klines_by_tf["5m"].get(ref_asset, {})
    risk_analysis = RiskAnalyzer.calculate_irq(ref_data.get("prices", []), ref_data.get("volumes", []))
    irq_score  = risk_analysis["irq_score"]
    protection = RiskAnalyzer.get_protection_level(irq_score)

    # ── 4. P&L por bucket ────────────────────────────────────────────────
    def _calc_pnl_bucket(top_list, klines, bucket_capital):
        if not top_list:
            return 0.0, {}
        per_asset = round(bucket_capital / len(top_list), 2)
        pnl = 0.0
        positions = {}
        for asset in top_list:
            prices = klines.get(asset, {}).get("prices", []) if klines else []
            if len(prices) >= 2 and prices[-2] != 0:
                ret = (prices[-1] - prices[-2]) / prices[-2]
            else:
                ret = 0.0
            pnl += per_asset * ret
            positions[asset] = {"amount": per_asset, "ret_pct": round(ret * 100, 3)}
        return round(pnl, 4), positions

    pnl_5m, pos_5m = _calc_pnl_bucket(top_5m, klines_by_tf["5m"], capital_5m)
    pnl_1h, pos_1h = _calc_pnl_bucket(top_1h, klines_by_tf["1h"], capital_1h)
    pnl_1d, pos_1d = _calc_pnl_bucket(top_1d, klines_by_tf["1d"], capital_1d)
    cycle_pnl = round(pnl_5m + pnl_1h + pnl_1d, 4)

    # ── 5. Montar posições unificadas ────────────────────────────────────
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

    _trade_state["positions"] = new_positions
    _trade_state["last_cycle"] = datetime.now().isoformat()
    _trade_state["total_pnl"] = round(_trade_state.get("total_pnl", 0.0) + cycle_pnl, 4)

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
    _trade_log("CICLO", "—", capital,
        f"🔄 [{session_label}] {data_source} | 5m(20%): R${pnl_5m:+.2f} | 1h(35%): R${pnl_1h:+.2f} | 1d(45%): R${pnl_1d:+.2f} | Total: R${cycle_pnl:+.2f} | IRQ: {irq_score:.3f}")

    _record_cycle_performance(cycle_pnl, capital, irq_score, pnl_5m, pnl_1h, pnl_1d)

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
        "pnl_5m":      pnl_5m,
        "pnl_1h":      pnl_1h,
        "pnl_1d":      pnl_1d,
        "capital_5m":  capital_5m,
        "capital_1h":  capital_1h,
        "capital_1d":  capital_1d,
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
            "total_auto_cycles":  _scheduler_state["total_auto_cycles"],
            "market_open_now":    _is_market_open(),
            "session":            _scheduler_state.get("session", _current_session()[1]),
            "b3_open":            _is_market_open(),
            "crypto_always_on":   True,
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

    # Calcular Sharpe dos ciclos
    pnls = [c["pnl"] for c in cycles]
    if len(pnls) >= 2:
        import statistics as _stats
        mu    = _stats.mean(pnls)
        sigma = _stats.stdev(pnls)
        sharpe = (mu / sigma) * (252 ** 0.5) if sigma > 0 else 0.0
    else:
        sharpe = 0.0

    total_pnl = sum(pnls)
    avg_daily = total_pnl / len(cycles) if cycles else 0.0

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
    # totais acumulados (da memória persistida, com fallback do cálculo instantâneo)
    total_gain_acc = _perf_state.get("total_gain") or round(sum(c.get("pnl", 0) for c in cycles if c.get("pnl", 0) > 0), 2)
    total_loss_acc = _perf_state.get("total_loss") or round(sum(abs(c.get("pnl", 0)) for c in cycles if c.get("pnl", 0) < 0), 2)

    return {
        "success": True,
        "data": {
            "total_cycles":      len(cycles),
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
            # P&L por timeframe — histórico total
            "pnl_total_5m":      pnl_total_5m,
            "pnl_total_1h":      pnl_total_1h,
            "pnl_total_1d":      pnl_total_1d,
            # Alocação vigente
            "alloc_5m_pct":      int(_TIMEFRAME_ALLOC["5m"] * 100),
            "alloc_1h_pct":      int(_TIMEFRAME_ALLOC["1h"] * 100),
            "alloc_1d_pct":      int(_TIMEFRAME_ALLOC["1d"] * 100),
        },
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
        _save_json(_PERF_FILE, _perf_state)

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
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    recent_critical = sum(1 for e in _audit_log if e.get("severity") == "critical" and e.get("timestamp", "") > cutoff)

    return {
        "success": True,
        "data": {
            "api_keys": {
                "brapi_configured": bool(settings.BRAPI_TOKEN),
                "binance_configured": bool(settings.BINANCE_API_KEY),
                "telegram_configured": bool(settings.TELEGRAM_BOT_TOKEN),
            },
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
            "risk_score": risk_manager.to_dict() if risk_manager else {},
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
                                       "/scheduler/start", "/scheduler/stop"]:
        _append_audit(
            f"{method} {path}",
            {"status_code": response.status_code},
            severity="warning" if "reset" in path else "info"
        )
    return response


# ─── 6. DADOS CONSOLIDADOS DO USUARIO ───────────────────────────────────────

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
                "total_cycles": len(cycles),
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


if __name__ == "__main__":
    import uvicorn
    import os

    port = int(os.getenv("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port, debug=settings.DEBUG)
