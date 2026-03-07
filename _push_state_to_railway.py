"""
PUSH STATE TO RAILWAY
Envia o estado histórico correto para o Railway via API.
Execute após o bot subir no Railway com DATABASE_URL configurada.
"""
import requests
import sys

RAILWAY_URL = "https://daytrade-bot-production.up.railway.app"

# ─── Valores históricos conhecidos ────────────────────────────────────────────
CAPITAL    = 3289.05
TOTAL_PNL  = 1449.06
WIN_COUNT  = 161
LOSS_COUNT = 318
TOTAL_GAIN = 2505.04
TOTAL_LOSS = 1055.98

# ──────────────────────────────────────────────────────────────────────────────
print(f"Conectando em {RAILWAY_URL} ...")

# 1. Health check
try:
    r = requests.get(f"{RAILWAY_URL}/health", timeout=15)
    r.raise_for_status()
    print(f"  ✅ Bot respondendo — {r.status_code}")
except Exception as e:
    print(f"  ❌ Bot não respondeu: {e}")
    sys.exit(1)

# 2. Restaurar trade_state (capital + total_pnl)
print("\nRestaurando trade_state (capital + total_pnl)...")
r = requests.post(f"{RAILWAY_URL}/admin/restore-trade", json={
    "capital":    CAPITAL,
    "total_pnl":  TOTAL_PNL,
    "auto_trading": True,
}, timeout=15)
d = r.json()
print(f"  capital   : R${d.get('capital', '?')}")
print(f"  total_pnl : R${d.get('total_pnl', '?')}")
print(f"  campos    : {d.get('updated_fields')}")

# 3. Restaurar performance state
print("\nRestaurando performance (win/loss/gain/loss/offsets)...")
r = requests.post(f"{RAILWAY_URL}/admin/restore-perf", json={
    "win_count":           WIN_COUNT,
    "loss_count":          LOSS_COUNT,
    "total_gain":          TOTAL_GAIN,
    "total_loss":          TOTAL_LOSS,
    "total_cycles_offset": WIN_COUNT + LOSS_COUNT,   # 479 — usado por _effective_total_cycles()
    "total_pnl_offset":    TOTAL_PNL,
    "best_day_pnl":        0.0,
    "worst_day_pnl":       0.0,
    "total_fees":          0.0,
}, timeout=15)
d = r.json()
print(f"  win       : {d.get('win_count')}  |  loss: {d.get('loss_count')}")
print(f"  gain      : R${d.get('total_gain')}  |  loss: R${d.get('total_loss')}")
print(f"  campos    : {d.get('updated_fields')}")

# 4. Verificação final
print("\nVerificando /performance no Railway...")
r = requests.get(f"{RAILWAY_URL}/performance", timeout=15)
pd = r.json()["data"]
print(f"  total_cycles : {pd.get('total_cycles')}")
print(f"  win_count    : {pd.get('win_count')}")
print(f"  loss_count   : {pd.get('loss_count')}")
print(f"  total_pnl    : R${pd.get('total_pnl')}")
print(f"  capital      : R${pd.get('current_capital')}")

print("\n✅ Pronto — Railway sincronizado com histórico correto.")
