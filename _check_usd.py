import httpx

url = "https://daytrade-bot.onrender.com"
state = httpx.get(f"{url}/trade-state", timeout=30).json()
broker = httpx.get(f"{url}/api/broker/status", timeout=30).json()

pnl = state.get("session", {}).get("pnl_brl", 0)
positions = state.get("positions", [])

USD_KEYWORDS = [
    "USDT", "BTC", "ETH", "SOL", "ADA", "DOT", "FIL", "NEAR",
    "AVAX", "MATIC", "LINK", "UNI", "ATOM", "DOGE", "XRP",
    "AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA",
    "SPY", "QQQ", "DIS", "NFLX", "JPM", "V", "WMT", "KO",
    "XAU", "XAG", "OIL", "EUR", "GBP", "JPY", "AUD", "CHF",
]

pnl_usd = 0
pnl_brl = 0

for p in positions:
    sym = p.get("symbol", "")
    pl = p.get("pnl_brl", 0) or 0
    if any(k in sym.upper() for k in USD_KEYWORDS):
        pnl_usd += pl
    else:
        pnl_brl += pl

cambio = 5.80
print(f"=== PnL Total: R${pnl:.2f} ===")
print(f"Ativos em USD (crypto/US/forex/commodities): R${pnl_usd:.2f}  =  US${pnl_usd/cambio:.2f}")
print(f"Ativos em BRL (B3): R${pnl_brl:.2f}")
print(f"Cambio usado: R${cambio}")
print()
print(f"Total posicoes: {len(positions)}")
print()

sorted_pos = sorted(positions, key=lambda x: x.get("pnl_brl", 0) or 0, reverse=True)
print("=== Top 5 lucros ===")
for p in sorted_pos[:5]:
    sym = p.get("symbol", "")
    pl = p.get("pnl_brl", 0) or 0
    pct = p.get("pnl_pct", 0) or 0
    is_usd = any(k in sym.upper() for k in USD_KEYWORDS)
    moeda = "USD" if is_usd else "BRL"
    usd_val = f" (US${pl/cambio:.2f})" if is_usd else ""
    print(f"  {sym}: R${pl:.2f} ({pct:+.2f}%) [{moeda}]{usd_val}")

print()
print(f"Binance balance: {broker.get('binance', {}).get('balance', '?')}")
print(f"Binance orders: {broker.get('binance', {}).get('orders_count', '?')}")
print(f"BTG balance: {broker.get('btg', {}).get('balance', '?')}")
