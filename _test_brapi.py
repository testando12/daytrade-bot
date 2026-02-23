import asyncio
import sys
import traceback
sys.path.insert(0, '.')

from app.market_data import BrapiMarketData
import httpx

svc = BrapiMarketData()

async def main():
    # Test 1: exact URL that get_all_prices builds
    tickers = "PETR4,VALE3,ITUB4"
    url = svc.BASE_URL + "/quote/" + tickers
    print("URL:", url)
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(url, params={"fundamental": "false"})
        print("Status:", r.status_code)
        data = r.json()
        print("Results:", len(data.get("results", [])))
        for item in data.get("results", []):
            print(f"  {item.get('symbol')} -> {item.get('regularMarketPrice')}")

    # Test 2: class method with exception visible
    print("\n--- get_all_prices ---")
    try:
        prices = await svc.get_all_prices(["PETR4", "VALE3", "ITUB4"])
        print("prices:", prices)
    except Exception:
        traceback.print_exc()

asyncio.run(main())
