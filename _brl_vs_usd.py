import requests

BASE = "https://daytrade-bot-production.up.railway.app"

# ---- PnL por timeframe ----
r = requests.get(f"{BASE}/performance", timeout=20)
d = r.json()["data"]

print("=== PnL por Timeframe ===")
print(f"  5m   : R${d['pnl_total_5m']:+.2f}")
print(f"  1h   : R${d['pnl_total_1h']:+.2f}")
print(f"  1d   : R${d['pnl_total_1d']:+.2f}")
print(f"  TOTAL: R${d['total_pnl']:+.2f}")
print()

# ---- Ultimos 10 ciclos ----
cycles = d["recent_cycles"]
print("=== Ultimos 10 ciclos ===")
for c in cycles[-10:]:
    print(f"  pnl={c['pnl']:+.2f}  5m={c['pnl_5m']:+.2f}  1h={c['pnl_1h']:+.2f}  1d={c['pnl_1d']:+.2f}")
print()

# ---- Market score: quais ativos estao ativos agora e em qual mercado ----
r2 = requests.get(f"{BASE}/market/score", timeout=20)
assets_raw = r2.json().get("data", {})

B3 = {"PETR4","VALE3","ITUB4","BBDC4","BBAS3","ABEV3","MGLU3","LREN3","WEGE3","EMBR3",
      "RENT3","JBSS3","SUZB3","VIVT3","RDOR3","PRIO3","CSAN3","EGIE3","GGBR4","ITSA4",
      "HAPV3","RAIL3","SBSP3","ENEV3","CCRO3"}

def get_market(asset):
    sym = asset.upper().replace("-USD","").replace("USDT","").replace(".SA","")
    if sym in B3 or asset.endswith(".SA"):
        return "B3/BRL"
    # Forex / commodities (5-char currency-like)
    if any(x in asset.upper() for x in ["EUR","GBP","JPY","AUD","CHF","XAU","XAG","OIL","WTI"]):
        return "FOREX/USD"
    # Provavelmente crypto ou US stock
    crypto_hints = {"BTC","ETH","BNB","SOL","ADA","XRP","DOT","LINK","AVAX","DOGE","MATIC","UNI","ATOM","FIL","NEAR"}
    if sym in crypto_hints or asset.endswith("USDT"):
        return "CRYPTO/USD"
    return "US/USD"

# Ordenar por score
if isinstance(assets_raw, dict):
    items = list(assets_raw.items())
    def score_of(x):
        v = x[1]
        if isinstance(v, dict):
            return v.get("momentum_score", 0) or 0
        return float(v) if isinstance(v, (int, float)) else 0

    items_sorted = sorted(items, key=score_of, reverse=True)

    brl_total = 0.0
    usd_total = 0.0
    print("=== Top 15 ativos por score ===")
    for a, v in items_sorted[:15]:
        scr = score_of((a, v))
        mkt = get_market(a)
        print(f"  {a:<12} score={scr:.3f}  [{mkt}]")
        if "BRL" in mkt:
            brl_total += scr
        else:
            usd_total += scr

    print()
    print(f"Soma scores BRL: {brl_total:.3f}")
    print(f"Soma scores USD: {usd_total:.3f}")
    total = brl_total + usd_total
    if total > 0:
        print(f"Dominancia BRL : {100*brl_total/total:.1f}%")
        print(f"Dominancia USD : {100*usd_total/total:.1f}%")
else:
    print("market/score retornou formato inesperado:")
    print(assets_raw)

# ---- Mostrar trade-state ----
print()
r3 = requests.get(f"{BASE}/trade-state", timeout=20)
ts = r3.json()
print("=== Trade State atual ===")
print(f"  capital          : R${ts.get('capital',0):.2f}")
print(f"  equity           : R${ts.get('equity',0):.2f}")
print(f"  pnl_total        : R${ts.get('total_pnl',0):.2f}")
posicoes = ts.get("positions") or ts.get("open_positions") or []
if posicoes:
    print(f"  posicoes abertas : {len(posicoes)}")
    for p in posicoes:
        sym = p.get("symbol","?")
        pl  = p.get("unrealized_pnl", p.get("pnl",0)) or 0
        mkt = get_market(sym)
        print(f"    {sym:<12} pnl={pl:+.2f}  [{mkt}]")
else:
    print("  sem posicoes abertas no momento")
