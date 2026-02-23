"""
Aplicação FastAPI principal - Day Trade Bot
"""

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
    "interval_minutes": 30,   # ciclo a cada 30 minutos
    "only_market_hours": True, # B3: seg-sex 10h-17h BRT; crypto ignora isso
    "next_run": None,
    "total_auto_cycles": 0,
    "task": None,
}

def _is_market_open() -> bool:
    """Verifica se o mercado B3 está aberto (seg-sex 10:00-17:00 BRT = UTC-3)."""
    from datetime import timezone, timedelta
    brt = timezone(timedelta(hours=-3))
    now = datetime.now(brt)
    if now.weekday() >= 5:   # sábado=5, domingo=6
        return False
    return 10 <= now.hour < 17


async def _auto_cycle_loop():
    """Loop interno do scheduler: executa ciclos de trading automaticamente."""
    _scheduler_state["running"] = True
    print("[scheduler] Iniciado — intervalo:", _scheduler_state["interval_minutes"], "min", flush=True)
    while _scheduler_state["running"]:
        interval_sec = _scheduler_state["interval_minutes"] * 60
        _scheduler_state["next_run"] = datetime.now().isoformat()

        # Verificar horário de mercado (se configurado)
        if _scheduler_state["only_market_hours"] and not _is_market_open():
            print("[scheduler] Fora do horário de mercado — aguardando...", flush=True)
        else:
            try:
                result = await _run_trade_cycle_internal()
                _scheduler_state["total_auto_cycles"] += 1
                pnl = result.get("cycle_pnl", 0)
                irq = result.get("irq", 0)
                print(
                    f"[scheduler] Ciclo #{_scheduler_state['total_auto_cycles']} "
                    f"| P&L estimado: R$ {pnl:.4f} | IRQ: {irq:.3f}",
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


@app.get("/")
async def root():
    """Endpoint raiz"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "message": "Bot de Day Trade Automatizado",
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
    """Análise completa: Momentum + Risco + Alocação"""
    try:
        # 1. Analisar Momentum
        momentum_results = MomentumAnalyzer.calculate_multiple_assets(test_assets_data)
        momentum_scores = {asset: data["momentum_score"] for asset, data in momentum_results.items()}

        # 2. Analisar Risco Global
        btc_data = test_assets_data["BTC"]
        risk_analysis = RiskAnalyzer.calculate_irq(
            btc_data["prices"],
            btc_data["volumes"],
        )
        irq_score = risk_analysis["irq_score"]
        protection = RiskAnalyzer.get_protection_level(irq_score)

        # 3. Calcular Alocação
        initial_capital = settings.INITIAL_CAPITAL
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
        raise HTTPException(status_code=503, detail="Serviço de dados de mercado não disponível")
    try:
        # 1. Buscar dados reais
        live_data = await market_data_service.get_all_klines(
            settings.ALL_ASSETS, interval, limit
        )
        if not live_data:
            raise HTTPException(status_code=502, detail="Não foi possível obter dados da Binance")

        # 2. Analisar Momentum
        momentum_results = MomentumAnalyzer.calculate_multiple_assets(live_data)
        momentum_scores = {asset: data["momentum_score"] for asset, data in momentum_results.items()}

        # 3. Analisar Risco (usando BTC como referência)
        btc_data = live_data.get("BTC", {})
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

        return {
            "success": True,
            "message": "Análise com dados REAIS da Binance",
            "source": "yahoo.finance",
            "data": {
                "timestamp": datetime.utcnow(),
                "interval": interval,
                "assets_analyzed": len(live_data),
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
                    "protection_level": protection["level"],
                    "color": protection["color"],
                    "signal_scores": {
                        "S1": risk_analysis["s1_trend_loss"],
                        "S2": risk_analysis["s2_selling_pressure"],
                        "S3": risk_analysis["s3_volatility"],
                        "S4": risk_analysis["s4_rsi_divergence"],
                        "S5": risk_analysis["s5_losing_streak"],
                    },
                    "rsi": risk_analysis["rsi"],
                },
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
    """Status do gerenciador de risco (stop loss, limites, P&L diário)"""
    return {
        "success": True,
        "message": "Status de risco operacional",
        "data": risk_manager.get_status(),
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
# garantir que o capital do settings bata com o persisted
settings.INITIAL_CAPITAL = _trade_state.get("capital", settings.INITIAL_CAPITAL)

# ── performance history ────────────────────────────────
_DEFAULT_PERF: dict = {
    "cycles": [],          # [{timestamp, pnl, capital, irq, wins, losses}]
    "total_pnl_history": [],
    "win_count": 0,
    "loss_count": 0,
    "best_day_pnl": 0.0,
    "worst_day_pnl": 0.0,
    "last_backtest": None,
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


def _record_cycle_performance(pnl: float, capital: float, irq: float):
    """Registra o P&L de um ciclo no histórico de performance."""
    _perf_state["cycles"].append({
        "timestamp": datetime.now().isoformat(),
        "pnl":       round(pnl, 4),
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
    elif pnl < 0:
        _perf_state["loss_count"] = _perf_state.get("loss_count", 0) + 1
        if pnl < _perf_state.get("worst_day_pnl", 0.0):
            _perf_state["worst_day_pnl"] = round(pnl, 4)

    _save_json(_PERF_FILE, _perf_state)


@app.get("/trade/status")
async def trade_status():
    """Retorna o estado atual do trading: capital, posições, log de eventos."""
    return {
        "success": True,
        "data": {
            "capital": _trade_state["capital"],
            "auto_trading": _trade_state["auto_trading"],
            "total_pnl": _trade_state["total_pnl"],
            "positions": _trade_state["positions"],
            "log": _trade_state["log"],
            "last_cycle": _trade_state["last_cycle"],
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


async def _run_trade_cycle_internal() -> dict:
    """Lógica interna de um ciclo de trading. Chamada pelo endpoint e pelo scheduler."""
    capital = _trade_state["capital"]

    # ── 1. Obter dados de mercado — B3 + Crypto ─────────────────────
    all_assets = settings.ALL_ASSETS  # B3 + crypto
    market_data = None
    data_source = "test"

    if MARKET_DATA_AVAILABLE and market_data_service:
        try:
            klines = await market_data_service.get_all_klines(
                all_assets, "5m", 25
            )
            if klines:
                market_data = klines
                data_source = "yahoo.finance"
        except Exception as e:
            _trade_log("ERRO", "—", 0, f"Falha ao buscar dados ao vivo: {e}. Usando dados de teste.")

    if not market_data:
        # Fallback: test data (B3 + Crypto)
        market_data = test_assets_data
        data_source = "test"

    # ── 2. Análise de Momentum ────────────────────────────────────────────
    momentum_results = MomentumAnalyzer.calculate_multiple_assets(market_data)
    if not momentum_results:
        _trade_log("ERRO", "—", 0, "Momentum analyzer retornou vazio")
        return {"success": False, "message": "Sem dados de mercado"}

    momentum_scores = {asset: data["momentum_score"] for asset, data in momentum_results.items()}

    # ── 3. Análise de Risco ───────────────────────────────────────────────
    # Use first available asset for risk reference
    ref_asset = list(market_data.keys())[0]
    ref_data = market_data[ref_asset]
    risk_analysis = RiskAnalyzer.calculate_irq(
        ref_data.get("prices", []),
        ref_data.get("volumes", []),
    )
    irq_score = risk_analysis["irq_score"]
    protection = RiskAnalyzer.get_protection_level(irq_score)

    # ── 4. Alocação de portfólio ──────────────────────────────────────────
    allocation = PortfolioManager.calculate_portfolio_allocation(
        momentum_scores, irq_score, capital
    )
    rebalancing = PortfolioManager.apply_rebalancing_rules(
        allocation, momentum_results, capital, irq_score
    )

    new_positions = {}
    cycle_events = []

    for asset, alloc in rebalancing.items():
        action      = alloc.get("action", "HOLD")
        rec_amount  = alloc.get("recommended_amount", 0)
        cur_amount  = alloc.get("current_amount", 0)
        change_pct  = alloc.get("change_percentage", 0)
        classif     = alloc.get("classification", "—")

        new_positions[asset] = {
            "amount":         round(rec_amount, 2),
            "action":         action,
            "pct":            round(rec_amount / capital * 100, 1) if capital > 0 else 0,
            "classification": classif,
            "change_pct":     round(change_pct, 2),
        }

        if action == "BUY" and rec_amount > cur_amount:
            buy_val = rec_amount - cur_amount
            cycle_events.append(("COMPRA", asset, buy_val,
                f"BUY {asset} • {classif} • Alocando R$ {buy_val:.2f} (IRQ: {irq_score:.3f})"))
        elif action == "SELL" and cur_amount > rec_amount:
            sell_val = cur_amount - rec_amount
            cycle_events.append(("VENDA", asset, sell_val,
                f"SELL {asset} • {classif} • Reduzindo R$ {sell_val:.2f} (IRQ: {irq_score:.3f})"))
        else:
            cycle_events.append(("HOLD", asset, rec_amount,
                f"HOLD {asset} • {classif} • Mantendo R$ {rec_amount:.2f}"))

    _trade_state["positions"] = new_positions
    _trade_state["last_cycle"] = datetime.now().isoformat()

    # Calcular P&L do ciclo (aproximação por variação das posições)
    cycle_pnl = sum(
        ev[2] * (1 if ev[0] == "COMPRA" else -1 if ev[0] == "VENDA" else 0)
        for ev in cycle_events
    )
    # Actualizar total_pnl acumulado
    _trade_state["total_pnl"] = round(
        _trade_state.get("total_pnl", 0.0) + cycle_pnl, 4
    )

    # Log do ciclo
    _trade_log("CICLO", "—", capital,
        f"🔄 Ciclo executado via {data_source} — {len(rebalancing)} ativos • IRQ: {irq_score:.3f} • Nível: {protection['level']}")
    for ev in cycle_events:
        _trade_log(*ev)

    # Registrar performance para histórico
    _record_cycle_performance(cycle_pnl, _trade_state["capital"], irq_score)

    # Salvar no banco se disponível
    if DB_AVAILABLE:
        try:
            db.save_analysis("trade_cycle", {
                "momentum": momentum_scores,
                "irq": irq_score,
                "capital": capital,
                "positions": new_positions,
            }, irq_score)
        except Exception:
            pass

    return {
        "positions":       new_positions,
        "irq":             round(irq_score, 4),
        "irq_level":       protection["level"],
        "assets_analyzed": len(rebalancing),
        "last_cycle":      _trade_state["last_cycle"],
        "data_source":     data_source,
        "cycle_pnl":       round(cycle_pnl, 4),
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

    return {
        "success": True,
        "data": {
            "total_cycles":     len(cycles),
            "win_count":        wins,
            "loss_count":       losses,
            "win_rate_pct":     round(wins / total * 100, 2) if total > 0 else 0.0,
            "total_pnl":        round(total_pnl, 2),
            "avg_pnl_per_cycle":round(avg_daily, 2),
            "best_cycle_pnl":   _perf_state.get("best_day_pnl", 0.0),
            "worst_cycle_pnl":  _perf_state.get("worst_day_pnl", 0.0),
            "max_drawdown_pct": round(max_dd, 4),
            "sharpe_ratio":     round(sharpe, 4),
            "equity_curve":     equity[-100:],   # últimos 100 pontos
            "recent_cycles":    cycles[-20:],    # últimos 20 ciclos
            "last_backtest":    _perf_state.get("last_backtest"),
            "current_capital":  _trade_state.get("capital"),
        },
    }


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


if __name__ == "__main__":
    import uvicorn
    import os

    port = int(os.getenv("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port, debug=settings.DEBUG)
