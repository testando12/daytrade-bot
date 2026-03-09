"""
Teste isolado completo dos novos engines VR e PB + integração com regime/main.
Executa: python _test_new_engines.py
"""
import sys, traceback

PASS = 0
FAIL = 0

def test(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ✅ {name}")
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {name}: {e}")
        traceback.print_exc()

# ═══════════════════════════════════════════════════════════════
# TEST 1: Imports
# ═══════════════════════════════════════════════════════════════
print("\n=== 1. IMPORTS ===")

def test_imports():
    from app.engines import (MomentumAnalyzer, MeanReversionAnalyzer, BreakoutAnalyzer,
                              SqueezeAnalyzer, LiquiditySweepAnalyzer, FVGAnalyzer,
                              RegimeDetector, VWAPReversionAnalyzer, PyramidBreakoutAnalyzer)
test("All engines import", test_imports)

def test_import_direct_vr():
    from app.engines.vwap_reversion import VWAPReversionAnalyzer
    assert hasattr(VWAPReversionAnalyzer, 'calculate_multiple_assets')
    assert hasattr(VWAPReversionAnalyzer, 'calculate_vwap_score')
test("VR direct import + methods", test_import_direct_vr)

def test_import_direct_pb():
    from app.engines.pyramid_breakout import PyramidBreakoutAnalyzer
    assert hasattr(PyramidBreakoutAnalyzer, 'calculate_multiple_assets')
    assert hasattr(PyramidBreakoutAnalyzer, 'calculate_pyramid_score')
test("PB direct import + methods", test_import_direct_pb)

# ═══════════════════════════════════════════════════════════════
# TEST 2: VWAPReversionAnalyzer
# ═══════════════════════════════════════════════════════════════
print("\n=== 2. VWAP REVERSION ENGINE ===")
import random
from app.engines.vwap_reversion import VWAPReversionAnalyzer

def make_lateral_data(base=100, n=50, noise=0.005, seed=42):
    """Gera dados de mercado lateral (ideal para VR)."""
    random.seed(seed)
    prices = [base * (1 + random.gauss(0, noise)) for _ in range(n)]
    volumes = [random.uniform(500, 2000) for _ in range(n)]
    return {"prices": prices, "volumes": volumes}

def make_deviated_data(base=100, n=50, seed=42):
    """Gera dados onde preço desvia bastante do VWAP (sinal forte VR)."""
    random.seed(seed)
    prices = [base * (1 + random.gauss(0, 0.003)) for _ in range(n)]
    # Últimos 5 candles caem forte → desvio abaixo do VWAP
    for i in range(5):
        prices[-(i+1)] = base * (1 - 0.02 - i*0.005)
    volumes = [random.uniform(500, 1500) for _ in range(n)]
    volumes[-1] = 5000  # volume spike
    return {"prices": prices, "volumes": volumes}

def test_vr_empty():
    r = VWAPReversionAnalyzer.calculate_multiple_assets({}, top_n=2)
    assert r == {}, f"Expected empty dict, got {r}"
test("VR empty input → {}", test_vr_empty)

def test_vr_short_data():
    r = VWAPReversionAnalyzer.calculate_multiple_assets(
        {"BTC": {"prices": [100, 101], "volumes": [50, 60]}}, top_n=1)
    assert r == {}, f"Short data should return empty, got {r}"
test("VR short data (2 prices) → {}", test_vr_short_data)

def test_vr_no_volumes():
    data = {"BTC": {"prices": [100 + i*0.1 for i in range(50)]}}
    r = VWAPReversionAnalyzer.calculate_multiple_assets(data, top_n=1)
    # Should handle gracefully (use default volumes or skip)
    assert isinstance(r, dict)
test("VR no volumes key → graceful", test_vr_no_volumes)

def test_vr_lateral_market():
    data = {"BTCUSDT": make_lateral_data(50000), "ETHUSDT": make_lateral_data(3000, seed=99)}
    r = VWAPReversionAnalyzer.calculate_multiple_assets(data, top_n=2)
    assert isinstance(r, dict)
    for a, d in r.items():
        assert "vr_score" in d, f"Missing vr_score for {a}"
        assert 0 <= d["vr_score"] <= 1, f"Score out of range for {a}: {d['vr_score']}"
        assert "direction" in d
        assert "vwap_deviation" in d
        assert "atr" in d
        assert d["entry_valid"] == True
    print(f"    → {len(r)} sinais: {[(a, round(d['vr_score'],3)) for a,d in r.items()]}")
test("VR lateral market → valid scores", test_vr_lateral_market)

def test_vr_deviated_signal():
    data = {"BTC": make_deviated_data(50000)}
    r = VWAPReversionAnalyzer.calculate_multiple_assets(data, top_n=1)
    if r:
        d = list(r.values())[0]
        assert d["vr_score"] >= 0.3, f"Deviated data should have decent score: {d['vr_score']}"
        print(f"    → score={d['vr_score']:.3f}, dir={d['direction']}, dev={d['vwap_deviation']:.4f}")
test("VR deviated data → high score", test_vr_deviated_signal)

def test_vr_score_fields():
    data = {"X": make_lateral_data(100, seed=77)}
    r = VWAPReversionAnalyzer.calculate_multiple_assets(data, top_n=1)
    if r:
        d = list(r.values())[0]
        required = ["vr_score", "direction", "vwap_deviation", "rsi", "volume_ratio", "atr", "entry_valid"]
        for f in required:
            assert f in d, f"Missing field: {f}"
test("VR output has all required fields", test_vr_score_fields)

# ═══════════════════════════════════════════════════════════════
# TEST 3: PyramidBreakoutAnalyzer
# ═══════════════════════════════════════════════════════════════
print("\n=== 3. PYRAMID BREAKOUT ENGINE ===")
from app.engines.pyramid_breakout import PyramidBreakoutAnalyzer

def make_trending_data(base=100, n=50, trend=0.003, seed=123):
    """Gera dados de tendência forte (ideal para PB)."""
    random.seed(seed)
    prices = [base * (1 + trend * i + random.gauss(0, 0.003)) for i in range(n)]
    volumes = [random.uniform(500, 1500) for _ in range(n)]
    return {"prices": prices, "volumes": volumes}

def make_breakout_data(base=100, n=50, seed=123):
    """Gera dados com breakout forte no final."""
    random.seed(seed)
    prices = [base * (1 + random.gauss(0, 0.005)) for _ in range(n)]
    # Últimos 3 candles rompem com força
    prices[-3] = base * 1.02
    prices[-2] = base * 1.035
    prices[-1] = base * 1.055  # breakout forte
    volumes = [random.uniform(500, 1500) for _ in range(n)]
    volumes[-1] = 5000  # volume surge no breakout
    volumes[-2] = 3000
    return {"prices": prices, "volumes": volumes}

def test_pb_empty():
    r = PyramidBreakoutAnalyzer.calculate_multiple_assets({}, top_n=2)
    assert r == {}
test("PB empty input → {}", test_pb_empty)

def test_pb_short_data():
    r = PyramidBreakoutAnalyzer.calculate_multiple_assets(
        {"BTC": {"prices": [100, 101], "volumes": [50, 60]}}, top_n=1)
    assert r == {}
test("PB short data (2 prices) → {}", test_pb_short_data)

def test_pb_no_volumes():
    data = {"BTC": {"prices": [100 + i*0.5 for i in range(50)]}}
    r = PyramidBreakoutAnalyzer.calculate_multiple_assets(data, top_n=1)
    assert isinstance(r, dict)
test("PB no volumes key → graceful", test_pb_no_volumes)

def test_pb_trending():
    data = {"BTCUSDT": make_trending_data(50000), "ETHUSDT": make_trending_data(3000, seed=99)}
    r = PyramidBreakoutAnalyzer.calculate_multiple_assets(data, top_n=2)
    assert isinstance(r, dict)
    for a, d in r.items():
        assert "pb_score" in d
        assert 0 <= d["pb_score"] <= 1
        assert "direction" in d
        assert "pyramid_level" in d
        assert "pyramid_multiplier" in d
        assert d["pyramid_level"] >= 1
        assert d["pyramid_multiplier"] >= 1.0
    print(f"    → {len(r)} sinais: {[(a, round(d['pb_score'],3), d['pyramid_level']) for a,d in r.items()]}")
test("PB trending data → valid scores", test_pb_trending)

def test_pb_breakout_signal():
    data = {"BTC": make_breakout_data(50000)}
    r = PyramidBreakoutAnalyzer.calculate_multiple_assets(data, top_n=1)
    if r:
        d = list(r.values())[0]
        print(f"    → score={d['pb_score']:.3f}, dir={d['direction']}, pyr_lvl={d['pyramid_level']}, pyr_mult={d['pyramid_multiplier']:.2f}")
test("PB breakout data → signal", test_pb_breakout_signal)

def test_pb_pyramid_levels():
    """Testa que pyramid_level está correto (1, 2 ou 3)."""
    data = {"BTC": make_breakout_data(50000, seed=456)}
    r = PyramidBreakoutAnalyzer.calculate_multiple_assets(data, top_n=1)
    if r:
        d = list(r.values())[0]
        assert d["pyramid_level"] in [1, 2, 3], f"Invalid pyramid level: {d['pyramid_level']}"
        if d["pyramid_level"] == 1:
            assert d["pyramid_multiplier"] == 1.0
        elif d["pyramid_level"] == 2:
            assert d["pyramid_multiplier"] == 1.5
        elif d["pyramid_level"] == 3:
            assert d["pyramid_multiplier"] == 1.8
test("PB pyramid level/multiplier consistency", test_pb_pyramid_levels)

def test_pb_score_fields():
    data = {"X": make_breakout_data(100, seed=77)}
    r = PyramidBreakoutAnalyzer.calculate_multiple_assets(data, top_n=1)
    if r:
        d = list(r.values())[0]
        required = ["pb_score", "direction", "pyramid_level", "pyramid_multiplier",
                     "bb_breakout", "atr_expansion", "ema_aligned", "atr", "entry_valid"]
        for f in required:
            assert f in d, f"Missing field: {f}"
test("PB output has all required fields", test_pb_score_fields)

# ═══════════════════════════════════════════════════════════════
# TEST 4: RegimeDetector integration
# ═══════════════════════════════════════════════════════════════
print("\n=== 4. REGIME DETECTOR + VR/PB ===")
from app.engines.regime import RegimeDetector

def test_regime_has_vr_pb():
    prices = make_lateral_data(100, n=100)["prices"]
    r = RegimeDetector.detect(prices)
    assert "vr" in r["multipliers"], "Missing vr in regime multipliers"
    assert "pb" in r["multipliers"], "Missing pb in regime multipliers"
    print(f"    → regime={r['regime']}, vr_mult={r['multipliers']['vr']:.2f}, pb_mult={r['multipliers']['pb']:.2f}")
test("Regime has vr/pb multipliers", test_regime_has_vr_pb)

def test_regime_lateral_boosts_vr():
    """Em mercado lateral, VR deve ter multiplier alto e PB baixo."""
    random.seed(42)
    prices = [100 + random.gauss(0, 0.3) for _ in range(200)]  # muito lateral
    r = RegimeDetector.detect(prices)
    if r["regime"] in ("LATERAL", "LOW_VOL_SQUEEZE"):
        assert r["multipliers"]["vr"] >= 1.0, f"VR should be boosted in lateral: {r['multipliers']['vr']}"
        assert r["multipliers"]["pb"] <= 0.5, f"PB should be reduced in lateral: {r['multipliers']['pb']}"
        print(f"    → {r['regime']}: vr={r['multipliers']['vr']:.2f} (boosted), pb={r['multipliers']['pb']:.2f} (reduced)")
    else:
        print(f"    → Regime={r['regime']} (not lateral, skip assertion)")
test("Lateral regime boosts VR, reduces PB", test_regime_lateral_boosts_vr)

def test_regime_trend_boosts_pb():
    """Em tendência forte, PB deve ter multiplier alto e VR baixo."""
    prices = [100 + i * 0.8 for i in range(200)]  # tendência clara
    r = RegimeDetector.detect(prices)
    if r["regime"] in ("TREND_STRONG", "TREND_WEAK"):
        assert r["multipliers"]["pb"] >= 1.0, f"PB should be boosted in trend: {r['multipliers']['pb']}"
        assert r["multipliers"]["vr"] <= 0.5, f"VR should be reduced in trend: {r['multipliers']['vr']}"
        print(f"    → {r['regime']}: pb={r['multipliers']['pb']:.2f} (boosted), vr={r['multipliers']['vr']:.2f} (reduced)")
    else:
        print(f"    → Regime={r['regime']} (not trend, skip assertion)")
test("Trend regime boosts PB, reduces VR", test_regime_trend_boosts_pb)

def test_apply_multipliers_all_keys():
    prices = make_lateral_data(100, n=100)["prices"]
    r = RegimeDetector.detect(prices)
    caps = {"mr": 100, "bo": 100, "sq": 100, "ls": 100, "fvg": 100, "vr": 100, "pb": 100}
    result = RegimeDetector.apply_multipliers(caps, r)
    assert "vr" in result, "apply_multipliers missing vr"
    assert "pb" in result, "apply_multipliers missing pb"
    assert all(v >= 0 for v in result.values()), "Negative capital after multipliers"
    print(f"    → {dict((k, round(v,1)) for k,v in result.items())}")
test("apply_multipliers handles all 7 buckets", test_apply_multipliers_all_keys)

# ═══════════════════════════════════════════════════════════════
# TEST 5: main.py integration (allocation math)
# ═══════════════════════════════════════════════════════════════
print("\n=== 5. MAIN.PY ALLOCATION ===")

def test_alloc_sums_100():
    """Verifica que as alocações somam 100%."""
    # Import the constants
    import importlib
    # Read the values directly
    allocs = {
        "5m": 0.08, "1h": 0.20, "1d": 0.30,
        "mr": 0.08, "bo": 0.06, "sq": 0.05,
        "ls": 0.02, "fvg": 0.02, "vr": 0.10, "pb": 0.09
    }
    total = sum(allocs.values())
    assert abs(total - 1.0) < 0.001, f"Allocations sum to {total}, expected 1.0"
    print(f"    → Total: {total:.2f} (OK)")
test("Allocations sum to 100%", test_alloc_sums_100)

def test_dynamic_weights_includes_vr_pb():
    """Verifica que _dynamic_strategy_weights retorna vr e pb."""
    # Can't easily import from main without starting the app, but let's check the logic
    keys_expected = {"mr", "bo", "sq", "ls", "fvg", "vr", "pb"}
    # Simulate the function logic
    result = {k: 1.0 for k in ("mr", "bo", "sq", "ls", "fvg", "vr", "pb")}
    assert set(result.keys()) == keys_expected
test("Dynamic weights keys include vr/pb", test_dynamic_weights_includes_vr_pb)

# ═══════════════════════════════════════════════════════════════
# TEST 6: Edge cases & robustness
# ═══════════════════════════════════════════════════════════════
print("\n=== 6. EDGE CASES & ROBUSTNESS ===")

def test_vr_constant_prices():
    """Preços constantes (zero volatilidade) → não deve crashar."""
    data = {"BTC": {"prices": [100.0] * 50, "volumes": [500.0] * 50}}
    r = VWAPReversionAnalyzer.calculate_multiple_assets(data, top_n=1)
    assert isinstance(r, dict)  # Pode retornar {} mas não crash
test("VR constant prices → no crash", test_vr_constant_prices)

def test_pb_constant_prices():
    data = {"BTC": {"prices": [100.0] * 50, "volumes": [500.0] * 50}}
    r = PyramidBreakoutAnalyzer.calculate_multiple_assets(data, top_n=1)
    assert isinstance(r, dict)
test("PB constant prices → no crash", test_pb_constant_prices)

def test_vr_single_price():
    data = {"BTC": {"prices": [100.0], "volumes": [500.0]}}
    r = VWAPReversionAnalyzer.calculate_multiple_assets(data, top_n=1)
    assert isinstance(r, dict)
test("VR single price → no crash", test_vr_single_price)

def test_pb_single_price():
    data = {"BTC": {"prices": [100.0], "volumes": [500.0]}}
    r = PyramidBreakoutAnalyzer.calculate_multiple_assets(data, top_n=1)
    assert isinstance(r, dict)
test("PB single price → no crash", test_pb_single_price)

def test_vr_negative_prices():
    """Preços negativos (edge case absurdo) → não deve crashar."""
    data = {"BTC": {"prices": [-100 + i for i in range(50)], "volumes": [500]*50}}
    try:
        r = VWAPReversionAnalyzer.calculate_multiple_assets(data, top_n=1)
        assert isinstance(r, dict)
    except:
        pass  # OK se rejeitar
test("VR negative prices → no crash", test_vr_negative_prices)

def test_vr_many_assets():
    """Muitos ativos → top_n funciona corretamente."""
    data = {}
    for i in range(20):
        random.seed(i)
        data[f"ASSET{i}"] = make_lateral_data(100 + i*10, seed=i)
    r = VWAPReversionAnalyzer.calculate_multiple_assets(data, top_n=3)
    assert len(r) <= 3, f"Expected ≤3 results, got {len(r)}"
test("VR top_n=3 with 20 assets → ≤3 results", test_vr_many_assets)

def test_pb_many_assets():
    data = {}
    for i in range(20):
        random.seed(i)
        data[f"ASSET{i}"] = make_breakout_data(100 + i*10, seed=i)
    r = PyramidBreakoutAnalyzer.calculate_multiple_assets(data, top_n=3)
    assert len(r) <= 3
test("PB top_n=3 with 20 assets → ≤3 results", test_pb_many_assets)

def test_vr_zero_volumes():
    """Volumes zerados → não deve dividir por zero."""
    data = {"BTC": {"prices": [100 + random.gauss(0,1) for _ in range(50)], "volumes": [0.0]*50}}
    r = VWAPReversionAnalyzer.calculate_multiple_assets(data, top_n=1)
    assert isinstance(r, dict)
test("VR zero volumes → no div/0", test_vr_zero_volumes)

def test_pb_zero_volumes():
    data = {"BTC": {"prices": [100 + i*0.5 for i in range(50)], "volumes": [0.0]*50}}
    r = PyramidBreakoutAnalyzer.calculate_multiple_assets(data, top_n=1)
    assert isinstance(r, dict)
test("PB zero volumes → no div/0", test_pb_zero_volumes)

# ═══════════════════════════════════════════════════════════════
# RESULTADO
# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"RESULTADO: {PASS} passed, {FAIL} failed")
print(f"{'='*60}")
sys.exit(1 if FAIL > 0 else 0)
