"""Análise detalhada de performance real do bot."""
import json, os
from collections import Counter
from datetime import datetime, timedelta

# Load trade state
try:
    with open("data/trade_state.json", "r") as f:
        ts = json.load(f)
    capital = ts.get("capital", 0)
    total_pnl = ts.get("total_pnl", 0)
    print("=== TRADE STATE ===")
    print(f"Capital base: R$ {capital:.2f}")
    print(f"Total PnL acumulado: R$ {total_pnl:.4f}")
    print(f"Capital efetivo: R$ {capital + total_pnl:.2f}")
    print(f"Retorno %: {total_pnl/capital*100:.4f}%")
    positions = ts.get("positions", {})
    print(f"Posicoes ativas: {len(positions)}")
    log = ts.get("log", [])
    print(f"Total log entries: {len(log)}")
    types = Counter(e.get("type", "?") for e in log)
    print(f"Log types: {dict(types)}")
    cycles = [e for e in log if e.get("type") == "CICLO"]
    entries = [e for e in log if e.get("type") == "ENTRY"]
    exits = [e for e in log if e.get("type") == "EXIT"]
    tps = [e for e in log if e.get("type") in ("TAKE_PROFIT_ATR", "PARTIAL_TP")]
    print(f"Total ciclos: {len(cycles)}")
    print(f"Total entradas: {len(entries)}")
    print(f"Total saidas: {len(exits)}")
    print(f"Total take profits: {len(tps)}")
    if cycles:
        print(f"Primeiro ciclo: {cycles[-1].get('timestamp', '?')}")
        print(f"Ultimo ciclo:   {cycles[0].get('timestamp', '?')}")
        # Parse timestamps to get duration
        try:
            first_ts = cycles[-1].get("timestamp", "")[:19]
            last_ts = cycles[0].get("timestamp", "")[:19]
            first_dt = datetime.fromisoformat(first_ts)
            last_dt = datetime.fromisoformat(last_ts)
            duration = last_dt - first_dt
            hours = duration.total_seconds() / 3600
            days = duration.days
            print(f"Duracao: {days} dias, {hours:.1f} horas")
            if hours > 0:
                print(f"PnL/hora: R$ {total_pnl/hours:.4f}")
                print(f"PnL/dia (24h): R$ {total_pnl/hours*24:.4f}")
                print(f"PnL/mes (30d): R$ {total_pnl/hours*24*30:.2f}")
        except:
            pass
except Exception as e:
    print(f"Erro trade_state: {e}")

print()

# Load performance
try:
    with open("data/performance.json", "r") as f:
        perf = json.load(f)
    print("=== PERFORMANCE ===")
    print(f"Total PnL: R$ {perf.get('total_pnl', 0):.4f}")
    print(f"Total cycles: {perf.get('total_cycles', 0)}")
    print(f"PnL today: R$ {perf.get('pnl_today', 0):.4f}")
    print(f"Today cycles: {perf.get('today_cycles', 0)}")
    print(f"PnL 5m: R$ {perf.get('pnl_today_5m', 0):.4f}")
    print(f"PnL 1h: R$ {perf.get('pnl_today_1h', 0):.4f}")
    print(f"PnL 1d: R$ {perf.get('pnl_today_1d', 0):.4f}")
    recent = perf.get("recent_cycles", [])
    print(f"Recent cycles stored: {len(recent)}")
    if recent:
        pnls = [c.get("pnl", 0) for c in recent]
        print(f"\n=== ESTATISTICAS POR CICLO ===")
        print(f"Total ciclos recentes: {len(pnls)}")
        print(f"PnL medio/ciclo: R$ {sum(pnls)/len(pnls):.4f}")
        print(f"PnL mediano/ciclo: R$ {sorted(pnls)[len(pnls)//2]:.4f}")
        print(f"Max PnL/ciclo: R$ {max(pnls):.4f}")
        print(f"Min PnL/ciclo: R$ {min(pnls):.4f}")
        positivos = [p for p in pnls if p > 0]
        negativos = [p for p in pnls if p < 0]
        zeros = [p for p in pnls if p == 0]
        print(f"Ciclos positivos: {len(positivos)} ({len(positivos)/len(pnls)*100:.1f}%)")
        print(f"Ciclos negativos: {len(negativos)} ({len(negativos)/len(pnls)*100:.1f}%)")
        print(f"Ciclos neutros: {len(zeros)}")
        if positivos:
            print(f"Media ganho: R$ {sum(positivos)/len(positivos):.4f}")
        if negativos:
            print(f"Media perda: R$ {sum(negativos)/len(negativos):.4f}")
        # Per-day analysis
        by_day = {}
        for c in recent:
            ts_str = c.get("timestamp", "")[:10]
            if ts_str:
                by_day.setdefault(ts_str, []).append(c.get("pnl", 0))
        print(f"\n=== PNL POR DIA ===")
        total_days = len(by_day)
        for day in sorted(by_day.keys()):
            day_pnl = sum(by_day[day])
            n = len(by_day[day])
            print(f"  {day}: R$ {day_pnl:+.4f} ({n} ciclos)")
        if total_days > 0:
            daily_pnls = [sum(v) for v in by_day.values()]
            avg_daily = sum(daily_pnls) / total_days
            print(f"\nMedia diaria: R$ {avg_daily:.4f}")
            print(f"Projecao mensal (30d): R$ {avg_daily * 30:.2f}")
            print(f"Dias positivos: {sum(1 for d in daily_pnls if d > 0)}/{total_days}")
except Exception as e:
    print(f"Erro performance: {e}")

# Compound growth projection
print("\n=== PROJECAO JUROS COMPOSTOS ===")
capital_base = 2000.0
# Use actual data if available
try:
    # daily return rate from real data
    if total_days > 0 and avg_daily > 0:
        daily_rate = avg_daily / capital_base
        print(f"Taxa diaria real observada: {daily_rate*100:.4f}%")
        print(f"Taxa mensal equivalente: {((1+daily_rate)**30-1)*100:.2f}%")
        print(f"Taxa anual equivalente: {((1+daily_rate)**365-1)*100:.1f}%")
        
        # Projection with compound reinvestment
        print(f"\n--- Projecao com reinvestimento (R$ {capital_base:.0f} inicial) ---")
        cap = capital_base
        for month in range(1, 37):
            monthly_return = cap * ((1 + daily_rate) ** 30 - 1)
            cap += monthly_return
            if month <= 12 or month % 6 == 0:
                print(f"  Mes {month:2d}: Capital R$ {cap:>12,.2f} | Ganho mensal: R$ {monthly_return:>10,.2f}")
            if monthly_return >= 15000 and month > 1:
                print(f"\n  >>> META R$ 15.000/mes atingida no mes {month}! <<<")
                print(f"  >>> Capital necessario: ~R$ {cap - monthly_return:,.2f} <<<")
                break
        else:
            # If not reached in 36 months, calculate when
            cap = capital_base
            for month in range(1, 500):
                monthly_return = cap * ((1 + daily_rate) ** 30 - 1)
                cap += monthly_return
                if monthly_return >= 15000:
                    print(f"\n  >>> META R$ 15.000/mes atingida no mes {month}! <<<")
                    print(f"  >>> Capital acumulado: R$ {cap:,.2f} <<<")
                    break
except:
    pass
