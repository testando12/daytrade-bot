"""Teste de endpoints locais."""
import urllib.request
import json

BASE = "https://daytrade-bot-production.up.railway.app"

endpoints = [
    ("GET", "/health"),
    ("GET", "/trade/status"),
    ("GET", "/scheduler/status"),
    ("GET", "/alerts/status"),
    ("GET", "/performance"),
    ("GET", "/db/status"),
]

ok_count = 0
fail_count = 0

for method, path in endpoints:
    try:
        req = urllib.request.Request(BASE + path, method=method)
        r = urllib.request.urlopen(req, timeout=15)
        data = json.loads(r.read().decode())
        info = ""
        if path == "/health":
            info = data.get("status", "")
        elif path == "/trade/status":
            d = data.get("data", data)
            cap = d.get("capital", "?")
            mode = d.get("trading_mode", "?")
            info = f"capital=R${cap} mode={mode}"
        elif path == "/scheduler/status":
            d = data.get("data", data)
            info = f"cycles={d.get('total_auto_cycles', 0)} running={d.get('running', '?')}"
        elif path == "/db/status":
            d = data.get("data", data)
            info = f"backend={d.get('backend', '?')}"
        elif path == "/alerts/status":
            d = data.get("data", data)
            channels = d.get("channels", {})
            info = f"canais={list(channels.keys())} total={d.get('total_alerts_sent', 0)}"
        elif path == "/performance":
            d = data.get("data", data)
            cycles = len(d.get("cycles", []))
            info = f"ciclos={cycles} win={d.get('win_count', 0)} loss={d.get('loss_count', 0)}"
        print(f"  OK  {path}: {info}")
        ok_count += 1
    except Exception as e:
        print(f"  ERRO {path}: {e}")
        fail_count += 1

print(f"\n=== Resultado: {ok_count} OK, {fail_count} ERRO ===")
