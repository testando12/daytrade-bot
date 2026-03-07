"""
Reconstroi trade_state.json e performance.json com os dados corretos
que foram capturados antes do Railway ser resetado.

Valores obtidos em 06/03/2026 antes do redeploy:
  capital = R$3289.05
  total_pnl = R$1449.06
  win_count = 161, loss_count = 318
  total_gain = 2505.04, total_loss = 1055.98
"""
import json, os
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ── trade_state.json ─────────────────────────────────────────────────────────
trade_path = os.path.join(DATA_DIR, "trade_state.json")
try:
    with open(trade_path, encoding="utf-8", errors="replace") as f:
        trade = json.load(f)
except Exception:
    trade = {}

# Sobrescreve apenas os campos críticos com os valores corretos
trade.update({
    "capital":              3289.05,
    "capital_base":         3289.05,
    "total_pnl":            1449.06,
    "pnl_today":            0.0,
    "win_count":            161,
    "loss_count":           318,
    "consecutive_losses":   0,
    "max_drawdown_reached": False,
    "daily_loss_limit_hit": False,
    "positions":            trade.get("positions", []),
    "auto_trading":         True,
    "log":                  [],
    "last_cycle":           None,
    "_restored_manual":     datetime.now().isoformat(),
})

with open(trade_path, "w", encoding="utf-8") as f:
    json.dump(trade, f, indent=2, ensure_ascii=True)
print(f"trade_state.json ✓  capital=R${trade['capital']:.2f} | total_pnl=R${trade['total_pnl']:.2f}")

# ── performance.json ─────────────────────────────────────────────────────────
perf_path = os.path.join(DATA_DIR, "performance.json")
perf = {
    "cycles":               [],      # sem histórico detalhado disponível
    "total_pnl_history":    [],
    "win_count":            161,
    "loss_count":           318,
    "best_day_pnl":         0.0,
    "worst_day_pnl":        0.0,
    "last_backtest":        None,
    "total_gain":           2505.04,
    "total_loss":           1055.98,
    "total_fees":           0.0,
    "total_brokerage":      0.0,
    "total_exchange_fees":  0.0,
    "total_spread":         0.0,
    "total_slippage":       0.0,
    "total_fx":             0.0,
    "total_min_fee_adj":    0.0,
    # Offsets históricos (ciclos anteriores à migração Railway → local)
    "total_cycles_offset":  479,     # 161 wins + 318 losses
    "total_pnl_offset":     1449.06, # P&L acumulado histórico
    "_restored_manual":     datetime.now().isoformat(),
}

with open(perf_path, "w", encoding="utf-8") as f:
    json.dump(perf, f, indent=2, ensure_ascii=True)
print(f"performance.json ✓  win={perf['win_count']} | loss={perf['loss_count']} | gain=R${perf['total_gain']:.2f}")
print("\nPronto. Agora reinicie o bot.")
