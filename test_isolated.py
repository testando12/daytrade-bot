"""Teste isolado de todos os módulos do bot."""
import asyncio
import os
import sys

ERRORS = []
PASSED = []

def test_section(name):
    print(f"\n{'='*60}")
    print(f"  TESTE: {name}")
    print(f"{'='*60}")

# ─────────────────────────────────────────────────
# 1. db_state
# ─────────────────────────────────────────────────
test_section("db_state")
try:
    import app.db_state as db
    print(f"  Backend: {db.storage_info()}")
    print(f"  Usa PG: {db.is_using_postgres()}")

    # save/load
    test_data = {"test_key": "test_value", "number": 42, "nested": {"a": 1}}
    db.save_state("_test_isolated", test_data)
    loaded = db.load_state("_test_isolated", {})
    assert loaded.get("test_key") == "test_value", f"esperava test_value, got {loaded}"
    assert loaded.get("number") == 42, f"esperava 42, got {loaded.get('number')}"
    assert loaded.get("nested", {}).get("a") == 1, "nested falhou"
    print("  save_state / load_state: OK")

    # wait_pg_ready (sem PG = retorna True)
    result = db.wait_pg_ready(max_wait=3, interval=1)
    print(f"  wait_pg_ready: {result}")

    # Cleanup
    test_file = db._DATA_DIR / "_test_isolated.json"
    if test_file.exists():
        os.remove(test_file)
    PASSED.append("db_state")
    print("  ✅ db_state: PASSOU")
except Exception as e:
    ERRORS.append(f"db_state: {e}")
    print(f"  ❌ db_state: FALHOU — {e}")

# ─────────────────────────────────────────────────
# 2. config
# ─────────────────────────────────────────────────
test_section("config")
try:
    from app.core.config import Settings
    s = Settings()
    assert s.INITIAL_CAPITAL == 2770.0, f"INITIAL_CAPITAL={s.INITIAL_CAPITAL}, esperava 2770"
    assert s.STOP_LOSS_PERCENTAGE == 0.015, f"SL={s.STOP_LOSS_PERCENTAGE}"
    assert s.TAKE_PROFIT_PERCENTAGE == 0.03, f"TP={s.TAKE_PROFIT_PERCENTAGE}"
    assert s.MAX_DRAWDOWN_PERCENTAGE == 0.10, f"DD={s.MAX_DRAWDOWN_PERCENTAGE}"
    assert s.MIN_POSITION_AMOUNT == 30.0, f"MIN_POS={s.MIN_POSITION_AMOUNT}"
    assert s.WHATSAPP_PHONE == "5513976033731", f"PHONE={s.WHATSAPP_PHONE}"
    assert s.WHATSAPP_APIKEY == "5582151", f"APIKEY={s.WHATSAPP_APIKEY}"
    assert s.TRADING_MODE in ("paper", "testnet", "live"), f"MODE={s.TRADING_MODE}"
    print(f"  INITIAL_CAPITAL: R${s.INITIAL_CAPITAL}")
    print(f"  SL={s.STOP_LOSS_PERCENTAGE*100}% TP={s.TAKE_PROFIT_PERCENTAGE*100}% R:R=1:{s.TAKE_PROFIT_PERCENTAGE/s.STOP_LOSS_PERCENTAGE:.0f}")
    print(f"  MAX_DRAWDOWN: {s.MAX_DRAWDOWN_PERCENTAGE*100}%")
    print(f"  MIN_POSITION: R${s.MIN_POSITION_AMOUNT}")
    print(f"  TRADING_MODE: {s.TRADING_MODE}")
    print(f"  WHATSAPP: ...{s.WHATSAPP_PHONE[-4:]}")
    PASSED.append("config")
    print("  ✅ config: PASSOU")
except Exception as e:
    ERRORS.append(f"config: {e}")
    print(f"  ❌ config: FALHOU — {e}")

# ─────────────────────────────────────────────────
# 3. alerts (CallMeBot, Telegram, Discord classes)
# ─────────────────────────────────────────────────
test_section("alerts")
try:
    from app.alerts import (
        AlertManager, AlertLevel, 
        TelegramAlert, DiscordAlert, CallMeBotAlert,
        alert_manager
    )
    
    # CallMeBotAlert class
    wpp = CallMeBotAlert("5511999999999", "123456")
    assert wpp.enabled == True, "CallMeBotAlert deveria estar enabled"
    assert wpp.phone == "5511999999999"
    assert wpp.api_url == "https://api.callmebot.com/whatsapp.php"
    print("  CallMeBotAlert init: OK")

    # CallMeBotAlert disabled
    wpp_off = CallMeBotAlert("", "")
    assert wpp_off.enabled == False, "deveria estar disabled"
    print("  CallMeBotAlert disabled: OK")

    # TelegramAlert
    tg = TelegramAlert("token", "chatid")
    assert tg.enabled == True
    tg_off = TelegramAlert("", "")
    assert tg_off.enabled == False
    print("  TelegramAlert: OK")

    # DiscordAlert
    dc = DiscordAlert("https://discord.com/webhook")
    assert dc.enabled == True
    dc_off = DiscordAlert("")
    assert dc_off.enabled == False
    print("  DiscordAlert: OK")

    # AlertManager
    mgr = AlertManager()
    mgr.add_whatsapp("w1", "5511999", "key123")
    mgr.add_telegram("t1", "tok", "chatid")
    mgr.add_discord("d1", "https://hook")
    assert len(mgr.channels) == 3, f"esperava 3 canais, got {len(mgr.channels)}"
    print(f"  AlertManager canais: {list(mgr.channels.keys())}")

    # Anti-spam
    async def test_antispam():
        # Primeiro envio: sempre passa (vai falhar no send, mas testar lógica)
        mgr2 = AlertManager()
        mgr2.channels = {}  # sem canais = sem erro
        r1 = await mgr2.send_alert("ev1", "t", "m")
        # Segundo envio com mesmo evento: bloqueado por anti-spam
        r2 = await mgr2.send_alert("ev1", "t", "m")
        assert r2 == False, "anti-spam deveria bloquear"
        print("  Anti-spam: OK")

    asyncio.run(test_antispam())

    # Métodos novos existem
    assert hasattr(alert_manager, "alert_cycle_result"), "falta alert_cycle_result"
    assert hasattr(alert_manager, "alert_daily_summary"), "falta alert_daily_summary"
    assert hasattr(alert_manager, "alert_critical_error"), "falta alert_critical_error"
    assert hasattr(alert_manager, "alert_stop_loss_triggered"), "falta alert_stop_loss_triggered"
    assert hasattr(alert_manager, "alert_trade_executed"), "falta alert_trade_executed"
    assert hasattr(alert_manager, "add_whatsapp"), "falta add_whatsapp"
    print("  Métodos novos: OK")

    # Status
    s = mgr.get_status()
    assert "channels" in s
    assert "total_alerts_sent" in s
    print("  get_status: OK")

    PASSED.append("alerts")
    print("  ✅ alerts: PASSOU")
except Exception as e:
    ERRORS.append(f"alerts: {e}")
    print(f"  ❌ alerts: FALHOU — {e}")

# ─────────────────────────────────────────────────
# 4. main.py imports
# ─────────────────────────────────────────────────
test_section("main.py imports")
try:
    # Verificar se os imports críticos funcionam
    from app.alerts import alert_manager, AlertLevel
    from app.core.config import Settings
    import app.db_state as db_state
    
    settings = Settings()
    
    # Verificar que as variáveis de WhatsApp estão configuradas
    assert settings.WHATSAPP_PHONE, "WHATSAPP_PHONE vazio"
    assert settings.WHATSAPP_APIKEY, "WHATSAPP_APIKEY vazio"
    
    # Simular o setup de canais que o lifespan faz
    test_mgr = AlertManager()
    if settings.WHATSAPP_PHONE and settings.WHATSAPP_APIKEY:
        ok = test_mgr.add_whatsapp("whatsapp_main", settings.WHATSAPP_PHONE, settings.WHATSAPP_APIKEY)
        assert ok == True, "add_whatsapp deveria retornar True"
        print(f"  WhatsApp channel: enabled={ok}")
    
    print("  Imports críticos: OK")
    PASSED.append("main imports")
    print("  ✅ main imports: PASSOU")
except Exception as e:
    ERRORS.append(f"main imports: {e}")
    print(f"  ❌ main imports: FALHOU — {e}")

# ─────────────────────────────────────────────────
# 5. Teste lógica de alert_cycle_result e alert_daily_summary
# ─────────────────────────────────────────────────
test_section("alert logic (cycle + daily)")
try:
    async def test_alert_logic():
        mgr = AlertManager()
        # Sem canais configurados — deve executar sem erro
        
        # alert_cycle_result com PnL zero: não deve enviar
        await mgr.alert_cycle_result(
            cycle_pnl=0.0, today_pnl=0.0, capital=2770,
            positions_count=0, fees=0.0, irq=0.3
        )
        assert len(mgr.alert_history) == 0, "PnL zero não deveria gerar alerta"
        print("  cycle_result PnL=0 (skip): OK")

        # alert_cycle_result com PnL > 0
        await mgr.alert_cycle_result(
            cycle_pnl=15.5, today_pnl=15.5, capital=2770,
            positions_count=3, fees=1.2, irq=0.45, session_label="Crypto24/7"
        )
        assert len(mgr.alert_history) == 1, f"esperava 1 alerta, got {len(mgr.alert_history)}"
        print("  cycle_result PnL=+15.5: OK")

        # alert_cycle_result com PnL < 0
        await mgr.alert_cycle_result(
            cycle_pnl=-5.2, today_pnl=10.3, capital=2770,
            positions_count=2, fees=0.8, irq=0.65
        )
        assert len(mgr.alert_history) == 2
        print("  cycle_result PnL=-5.2: OK")

        # alert_daily_summary
        await mgr.alert_daily_summary(
            today_pnl=25.0, capital=2770, total_cycles=48,
            win_cycles=30, date_str="02/03/2026"
        )
        assert len(mgr.alert_history) == 3
        last = mgr.alert_history[-1]
        assert "Resumo" in last["title"], f"título errado: {last['title']}"
        print("  daily_summary: OK")

        # alert_critical_error
        await mgr.alert_critical_error("Connection timeout", "DB_ERROR")
        assert len(mgr.alert_history) == 4
        last = mgr.alert_history[-1]
        assert "CRÍTICO" in last["title"]
        print("  critical_error: OK")

        # alert_daily_summary com performance ruim (recomendação)
        mgr2 = AlertManager()
        await mgr2.alert_daily_summary(
            today_pnl=-150.0, capital=2770, total_cycles=48,
            win_cycles=10, date_str="02/03/2026"
        )
        last = mgr2.alert_history[-1]
        assert "suspender" in last["message"].lower() or "ruim" in last["message"].lower(), \
            f"Esperava recomendação negativa, got: {last['message']}"
        print("  daily_summary recomendação negativa: OK")

    asyncio.run(test_alert_logic())
    PASSED.append("alert logic")
    print("  ✅ alert logic: PASSOU")
except Exception as e:
    ERRORS.append(f"alert logic: {e}")
    print(f"  ❌ alert logic: FALHOU — {e}")

# ─────────────────────────────────────────────────
# 6. Teste _record_cycle_performance safety check
# ─────────────────────────────────────────────────
test_section("perf_state safety")
try:
    # Verifica que a lógica de nunca sobrescrever DB com menos dados existe
    import inspect
    # Importar a função diretamente não é possível sem o app inteiro,
    # mas podemos verificar no source code
    with open("app/main.py", "r", encoding="utf-8") as f:
        src = f.read()
    
    assert "BLOQUEADO: DB tem" in src, "Safety check missing in _record_cycle_performance"
    print("  Safety check no _record_cycle_performance: encontrado")
    
    assert "wait_pg_ready" in src, "wait_pg_ready missing in lifespan"
    print("  wait_pg_ready no lifespan: encontrado")
    
    assert "db_capital >= mem_capital" in src, "Capital non-regression check missing"
    print("  Capital non-regression check: encontrado")
    
    assert "WHATSAPP_PHONE" in src, "WHATSAPP config missing in lifespan"
    print("  WhatsApp setup in lifespan: encontrado")
    
    assert "alert_cycle_result" in src, "alert_cycle_result hook missing"
    print("  alert_cycle_result hook: encontrado")
    
    assert "alert_daily_summary" in src, "alert_daily_summary hook missing"
    print("  alert_daily_summary hook: encontrado")
    
    assert "alert_critical_error" in src, "alert_critical_error hook missing"
    print("  alert_critical_error hook: encontrado")
    
    assert "_last_daily_summary_date" in src, "daily summary date tracker missing"
    print("  _last_daily_summary_date tracker: encontrado")

    PASSED.append("perf_state safety")
    print("  ✅ perf_state safety: PASSOU")
except Exception as e:
    ERRORS.append(f"perf_state safety: {e}")
    print(f"  ❌ perf_state safety: FALHOU — {e}")

# ─────────────────────────────────────────────────
# RESULTADO FINAL
# ─────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"  RESULTADO FINAL")
print(f"{'='*60}")
print(f"  ✅ Passaram: {len(PASSED)}/{len(PASSED)+len(ERRORS)}")
for p in PASSED:
    print(f"     ✅ {p}")
if ERRORS:
    print(f"  ❌ Falharam: {len(ERRORS)}")
    for e in ERRORS:
        print(f"     ❌ {e}")
else:
    print(f"\n  🎉 TODOS OS TESTES PASSARAM!")
print(f"{'='*60}")
