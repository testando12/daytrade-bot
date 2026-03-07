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
    perf = r2.json().get("data", {})
    print(f"      total_cycles = {perf.get('total_cycles', 0)}")
    print(f"      win_rate = {perf.get('win_rate', 0):.1f}%")

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
    perf_path = os.path.join(DATA_DIR, "performance.json")
    perf["_synced_from_railway"] = datetime.now().isoformat()
    with open(perf_path, "w", encoding="utf-8") as f:
        json.dump(perf, f, indent=2, ensure_ascii=True)
    print(f"      performance.json ✓ ({perf.get('total_cycles', 0)} ciclos)")

    print("\n" + "=" * 55)
    print("  SYNC CONCLUÍDO — agora rode: .\\iniciar_bot.ps1")
    print("=" * 55)

except Exception as e:
    print(f"\n[ERRO] {e}")
    print("O Railway pode estar offline ou a URL mudou.")
    print("Rode o bot local mesmo assim — começa com estado do JSON local.")
    sys.exit(1)
