"""
db_state.py — Persistência de estado do bot (trade_state + performance)

Estratégia:
  - Se DATABASE_URL começa com "postgres", usa PostgreSQL via psycopg2
  - Caso contrário, salva em JSON local (data/<key>.json)

Tabela criada automaticamente:
  bot_kv (key TEXT PRIMARY KEY, value TEXT)
"""

import json
import os
import logging
from pathlib import Path

log = logging.getLogger("db_state")

_DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"
_STATE_DIR_ENV = os.getenv("STATE_DIR") or os.getenv("RENDER_DISK_PATH")
if _STATE_DIR_ENV:
    _DATA_DIR = Path(_STATE_DIR_ENV)
elif Path("/var/data").exists():
    _DATA_DIR = Path("/var/data/daytrade")
else:
    _DATA_DIR = _DEFAULT_DATA_DIR

# Normaliza URL do Render (postgres:// → postgresql://)
_DB_URL = os.getenv("DATABASE_URL", "")
if _DB_URL.startswith("postgres://"):
    _DB_URL = _DB_URL.replace("postgres://", "postgresql://", 1)

_USE_PG = _DB_URL.startswith("postgresql://") or _DB_URL.startswith("postgres://")
_table_ready = False


# ─── PostgreSQL helpers ────────────────────────────────────────────────────────

def _pg_conn():
    import psycopg2
    return psycopg2.connect(_DB_URL)


def _ensure_table():
    global _table_ready
    if _table_ready:
        return
    try:
        conn = _pg_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS bot_kv (
                        key   TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)
        conn.close()
        _table_ready = True
        log.info("bot_kv table ready")
    except Exception as e:
        log.error(f"db_state _ensure_table error: {e}")


# ─── Public API ────────────────────────────────────────────────────────────────

def load_state(key: str, default: dict) -> dict:
    """Carrega estado do PostgreSQL ou do JSON local."""
    if _USE_PG:
        try:
            _ensure_table()
            conn = _pg_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM bot_kv WHERE key = %s", (key,))
                row = cur.fetchone()
            conn.close()
            if row:
                log.info(f"Loaded '{key}' from PostgreSQL")
                return json.loads(row[0])
        except Exception as e:
            log.error(f"db_state load_state({key}) PG error: {e} — falling back to JSON")

    # Fallback JSON
    path = _DATA_DIR / f"{key}.json"
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return dict(default)


def save_state(key: str, obj: dict):
    """Salva estado no PostgreSQL e também no JSON local (backup)."""
    if _USE_PG:
        try:
            _ensure_table()
            conn = _pg_conn()
            with conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO bot_kv (key, value)
                        VALUES (%s, %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    """, (key, json.dumps(obj, default=str)))
            conn.close()
        except Exception as e:
            log.error(f"db_state save_state({key}) PG error: {e} — falling back to JSON")

    # Sempre salva JSON local como backup
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        path = _DATA_DIR / f"{key}.json"
        path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def is_using_postgres() -> bool:
    return _USE_PG


def storage_info() -> dict:
    return {
        "backend": "postgres" if _USE_PG else "json",
        "data_dir": str(_DATA_DIR),
        "database_url_configured": bool(_DB_URL),
    }
