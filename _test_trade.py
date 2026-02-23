"""Test script: deposit 150, start bot, run 3 trade cycles, show log"""
import httpx, json, time

base = "http://localhost:8001"

def pprint(label, data):
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")
    if isinstance(data, dict):
        print(json.dumps(data, indent=2, ensure_ascii=False)[:800])
    else:
        print(data)

# 1. Depositar R$ 150
print("\n>>> STEP 1: Depositando R$ 150...")
r = httpx.post(f"{base}/trade/capital", json={"amount": 150}, timeout=5)
pprint("DEPÓSITO", r.json())

# 2. Iniciar bot
print("\n>>> STEP 2: Iniciando bot...")
r = httpx.post(f"{base}/trade/start", timeout=5)
pprint("BOT START", r.json())

# 3. Ciclo 1
print("\n>>> STEP 3: Executando CICLO 1...")
r = httpx.post(f"{base}/trade/cycle", timeout=30)
d = r.json()
if d.get("success"):
    data = d["data"]
    print(f"\n  Ativos analisados : {data['assets_analyzed']}")
    print(f"  IRQ Score         : {data['irq']} ({data.get('irq_level','?')})")
    print(f"  Fonte de dados    : {data.get('data_source','?')}")
    print(f"\n  Posicoes:")
    for asset, p in data["positions"].items():
        print(f"    {asset:<8} R${p['amount']:>7.2f}  ({p['pct']:>5.1f}%)  {p['action']:<5}  {p['classification']}")
else:
    print("  FALHOU:", d)

# Small delay between cycles
time.sleep(1)

# 4. Ciclo 2 (simula mudança de mercado)
print("\n>>> STEP 4: Executando CICLO 2...")
r = httpx.post(f"{base}/trade/cycle", timeout=30)
d = r.json()
if d.get("success"):
    print(f"  OK — {d['data']['assets_analyzed']} ativos | IRQ: {d['data']['irq']}")
else:
    print("  FALHOU:", d)

time.sleep(1)

# 5. Ciclo 3
print("\n>>> STEP 5: Executando CICLO 3...")
r = httpx.post(f"{base}/trade/cycle", timeout=30)
d = r.json()
if d.get("success"):
    print(f"  OK — {d['data']['assets_analyzed']} ativos | IRQ: {d['data']['irq']}")
else:
    print("  FALHOU:", d)

# 6. Estado final: log completo
print("\n>>> STEP 6: Estado final do bot...")
r = httpx.get(f"{base}/trade/status", timeout=5)
state = r.json()["data"]
print(f"\n  Capital     : R$ {state['capital']:.2f}")
print(f"  Bot ativo   : {state['auto_trading']}")
print(f"  Ultimo ciclo: {state['last_cycle']}")
print(f"  Log entries : {len(state['log'])}")
print(f"\n  === HISTORICO (ultimas 15 entradas) ===")
for ev in state["log"][:15]:
    ts = ev["timestamp"][:19].replace("T", " ")
    amt = f"R$ {ev['amount']:.2f}" if ev["amount"] > 0 else "       —"
    print(f"  [{ts}]  {ev['type']:<10} {ev['asset']:<8} {amt}  {ev['note'][:60]}")

print("\n\nTeste concluido com sucesso!")
