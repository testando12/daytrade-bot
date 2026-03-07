"""
Baixa o estado atual do Railway e salva nos JSONs locais.
Execute UMA VEZ antes de migrar para o bot local.

Uso: python _sync_from_railway.py
"""
import requests, json, os, sys
from datetime import datetime

RAILWAY_URL = "https://daytrade-bot-production.up.railway.app"
DATA_DIR    = os.path.join(os.path.dirname(__file__), "data")

print("=" * 55)
print("  SYNC RAILWAY → LOCAL")
print("=" * 55)

try:
    print("\n[1/3] Buscando /trade/status...")
    r = requests.get(f"{RAILWAY_URL}/trade/status", timeout=20)
    trade = r.json().get("data", {})
    print(f"      capital = R${trade.get('capital', 0):.2f}")
    print(f"      pnl_total = R${trade.get('total_pnl', 0):.2f}")

    print("\n[2/3] Buscando /performance...")
    r2 = requests.get(f"{RAILWAY_URL}/performance", timeout=20)
    perf_api = r2.json().get("data", {})
    print(f"      total_cycles = {perf_api.get('total_cycles', 0)}")
    print(f"      win_rate = {perf_api.get('win_rate', 0):.1f}%")

    print("\n[3/3] Salvando arquivos locais...")

    # ----- trade_state.json -----
    trade_path = os.path.join(DATA_DIR, "trade_state.json")
    if os.path.exists(trade_path):
        try:
            with open(trade_path, encoding="utf-8", errors="replace") as f:
                local_trade = json.load(f)
        except Exception:
            local_trade = {}
    else:
        local_trade = {}

    # Atualiza campos críticos de capital e P&L
    fields_to_sync = [
        "capital", "capital_base", "total_pnl", "pnl_today",
        "win_count", "loss_count", "total_cycles",
        "consecutive_losses", "max_drawdown_reached",
        "daily_loss_limit_hit", "positions",
    ]
    for k in fields_to_sync:
        if k in trade:
            local_trade[k] = trade[k]
    local_trade["_synced_from_railway"] = datetime.now().isoformat()

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(trade_path, "w", encoding="utf-8") as f:
        json.dump(local_trade, f, indent=2, ensure_ascii=True)
    print(f"      trade_state.json ✓ (capital=R${local_trade.get('capital', 0):.2f})")

    # ----- performance.json -----
    # Converte do formato da API para o formato interno do _perf_state do bot
    perf_path = os.path.join(DATA_DIR, "performance.json")
    perf_internal = {
        "cycles":               perf_api.get("recent_cycles", []),   # API usa recent_cycles
        "total_pnl_history":    [],
        "win_count":            perf_api.get("win_count", 0),
        "loss_count":           perf_api.get("loss_count", 0),
        "best_day_pnl":         perf_api.get("best_cycle_pnl", 0.0),
        "worst_day_pnl":        perf_api.get("worst_cycle_pnl", 0.0),
        "last_backtest":        perf_api.get("last_backtest", None),
        "total_gain":           perf_api.get("total_gain", 0.0),
        "total_loss":           perf_api.get("total_loss", 0.0),
        "total_fees":           0.0,
        "total_brokerage":      0.0,
        "total_exchange_fees":  0.0,
        "total_spread":         0.0,
        "total_slippage":       0.0,
        "total_fx":             0.0,
        "total_min_fee_adj":    0.0,
        "_synced_from_railway": datetime.now().isoformat(),
        "_total_cycles_railway": perf_api.get("total_cycles", 0),   # referência histórica
    }
    with open(perf_path, "w", encoding="utf-8") as f:
        json.dump(perf_internal, f, indent=2, ensure_ascii=True)
    n_cycles = len(perf_internal["cycles"])
    print(f"      performance.json ✓ ({n_cycles} ciclos recentes | win={perf_internal['win_count']} | loss={perf_internal['loss_count']})")

    print("\n" + "=" * 55)
    print("  SYNC CONCLUÍDO — agora rode: .\\iniciar_bot.ps1")
    print("=" * 55)

except Exception as e:
    print(f"\n[ERRO] {e}")
    print("O Railway pode estar offline ou a URL mudou.")
    print("Rode o bot local mesmo assim — começa com estado do JSON local.")
    sys.exit(1)
