"""Converte performance.json do formato API para formato interno do bot."""
import json

with open('data/performance.json', encoding='utf-8') as f:
    api = json.load(f)

internal = {
    'cycles': api.get('recent_cycles', []),
    'total_pnl_history': [],
    'win_count': api.get('win_count', 0),
    'loss_count': api.get('loss_count', 0),
    'best_day_pnl': api.get('best_cycle_pnl', 0.0),
    'worst_day_pnl': api.get('worst_cycle_pnl', 0.0),
    'last_backtest': api.get('last_backtest', None),
    'total_gain': api.get('total_gain', 0.0),
    'total_loss': api.get('total_loss', 0.0),
    'total_fees': 0.0,
    'total_brokerage': 0.0,
    'total_exchange_fees': 0.0,
    'total_spread': 0.0,
    'total_slippage': 0.0,
    'total_fx': 0.0,
    'total_min_fee_adj': 0.0,
}

with open('data/performance.json', 'w', encoding='utf-8') as f:
    json.dump(internal, f, indent=2, ensure_ascii=True)

print(f"OK - cycles: {len(internal['cycles'])} | win: {internal['win_count']} | loss: {internal['loss_count']}")
