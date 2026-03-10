"""
Reset capital para R$450 - preservando histórico de ciclos para análise.
Gera backup antes de qualquer mudança.
"""
import json, shutil
from datetime import datetime

# ── 1. Backups ────────────────────────────────────────────
shutil.copy('data/trade_state.json',  'data/trade_state_backup_Mar9_2026.json')
shutil.copy('data/performance.json',   'data/performance_backup_Mar9_2026.json')
print('[backup] trade_state_backup_Mar9_2026.json criado')
print('[backup] performance_backup_Mar9_2026.json criado')

# ── 2. Carrega estado atual ───────────────────────────────
with open('data/trade_state.json', encoding='utf-8') as f:
    state = json.load(f)

capital_anterior = state.get('capital', 0)
total_pnl_anterior = state.get('total_pnl', 0)

# ── 3. Reseta só o capital (mantém log completo) ──────────
state['capital']   = 450.0
state['total_pnl'] = 0.0   # PnL zera para nova simulação

# Marcador no topo do log
marker = {
    'timestamp': datetime.now().astimezone().isoformat(),
    'type':  'RESET_CAPITAL',
    'asset': '—',
    'amount': 450.0,
    'note': (
        f'🔄 NOVA SIMULAÇÃO R$450 | '
        f'Capital anterior: R${capital_anterior:.2f} | '
        f'PnL anterior: R${total_pnl_anterior:.2f} | '
        f'Histórico completo preservado em trade_state_backup_Mar9_2026.json'
    )
}
state['log'].insert(0, marker)

# ── 4. Salva ──────────────────────────────────────────────
with open('data/trade_state.json', 'w', encoding='utf-8') as f:
    json.dump(state, f, ensure_ascii=False, indent=2)

print(f'[ok] Capital resetado: R${capital_anterior:.2f} → R$450.00')
print(f'[ok] Log preservado: {len(state["log"])} entradas')
print(f'[ok] Performance histórica: data/performance_backup_Mar9_2026.json')
print()
print('Agora atualize o .env: INITIAL_CAPITAL=450')
print('E reinicie o bot.')
