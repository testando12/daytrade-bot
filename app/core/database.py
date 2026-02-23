"""
Banco de Dados - SQLite com migração futura para PostgreSQL
Armazena: portfolios, posições, trades, histórico de análises

Spec (Lógica de programação.md):
- Salvar: usuários, histórico de operações, carteira, dados do mercado, decisões da IA
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from app.core.config import settings


class Database:
    """Gerenciador de banco de dados SQLite"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = settings.DATABASE_URL.replace("sqlite:///", "")
        self.db_path = db_path

        # Garantir que o diretório existe
        db_dir = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(db_dir, exist_ok=True)

        self._init_tables()

    def _get_connection(self) -> sqlite3.Connection:
        """Cria conexão com row_factory"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        """Cria tabelas se não existirem"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT 'Principal',
                total_capital REAL NOT NULL,
                current_balance REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                is_active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id INTEGER NOT NULL,
                asset TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 0,
                entry_price REAL NOT NULL,
                current_price REAL NOT NULL DEFAULT 0,
                allocated_amount REAL NOT NULL DEFAULT 0,
                unrealized_pnl REAL NOT NULL DEFAULT 0,
                entered_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                is_active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
            );

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id INTEGER NOT NULL,
                asset TEXT NOT NULL,
                trade_type TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                total_value REAL NOT NULL,
                reason TEXT DEFAULT '',
                momentum_score REAL,
                irq_score REAL,
                executed_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
            );

            CREATE TABLE IF NOT EXISTS analysis_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                irq_score REAL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset TEXT NOT NULL,
                price REAL NOT NULL,
                volume REAL DEFAULT 0,
                change_24h REAL DEFAULT 0,
                captured_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_trades_asset ON trades(asset);
            CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(executed_at);
            CREATE INDEX IF NOT EXISTS idx_positions_active ON positions(is_active);
            CREATE INDEX IF NOT EXISTS idx_snapshots_asset ON market_snapshots(asset);
        """)

        conn.commit()
        conn.close()

    # ─────────────────────────────────────────
    # PORTFOLIO
    # ─────────────────────────────────────────

    def create_portfolio(self, name: str = "Principal", capital: float = None) -> int:
        """Cria um portfolio e retorna o ID"""
        if capital is None:
            capital = settings.INITIAL_CAPITAL

        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO portfolios (name, total_capital, current_balance) VALUES (?, ?, ?)",
            (name, capital, capital),
        )
        portfolio_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return portfolio_id

    def get_portfolio(self, portfolio_id: int = 1) -> Optional[Dict]:
        """Obtém portfolio por ID"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM portfolios WHERE id = ?", (portfolio_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_or_create_portfolio(self) -> Dict:
        """Obtém portfolio padrão ou cria um novo"""
        portfolio = self.get_portfolio(1)
        if portfolio is None:
            pid = self.create_portfolio()
            portfolio = self.get_portfolio(pid)
        return portfolio

    def update_balance(self, portfolio_id: int, new_balance: float):
        """Atualiza saldo do portfolio"""
        conn = self._get_connection()
        conn.execute(
            "UPDATE portfolios SET current_balance = ?, updated_at = datetime('now') WHERE id = ?",
            (new_balance, portfolio_id),
        )
        conn.commit()
        conn.close()

    # ─────────────────────────────────────────
    # TRADES
    # ─────────────────────────────────────────

    def record_trade(
        self,
        asset: str,
        trade_type: str,
        quantity: float,
        price: float,
        reason: str = "",
        momentum_score: float = None,
        irq_score: float = None,
        portfolio_id: int = 1,
    ) -> int:
        """Registra um trade executado"""
        total_value = quantity * price
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO trades 
               (portfolio_id, asset, trade_type, quantity, price, total_value, reason, momentum_score, irq_score) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (portfolio_id, asset, trade_type, quantity, price, total_value, reason, momentum_score, irq_score),
        )
        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return trade_id

    def get_trades(self, limit: int = 50, asset: str = None) -> List[Dict]:
        """Obtém histórico de trades"""
        conn = self._get_connection()
        cursor = conn.cursor()

        if asset:
            cursor.execute(
                "SELECT * FROM trades WHERE asset = ? ORDER BY executed_at DESC LIMIT ?",
                (asset, limit),
            )
        else:
            cursor.execute(
                "SELECT * FROM trades ORDER BY executed_at DESC LIMIT ?",
                (limit,),
            )

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_daily_trades_count(self) -> int:
        """Conta trades do dia"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as count FROM trades WHERE date(executed_at) = date('now')"
        )
        row = cursor.fetchone()
        conn.close()
        return row["count"] if row else 0

    # ─────────────────────────────────────────
    # ANÁLISES
    # ─────────────────────────────────────────

    def save_analysis(self, analysis_type: str, data: Dict, irq_score: float = None) -> int:
        """Salva uma análise no histórico"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO analysis_history (analysis_type, data_json, irq_score) VALUES (?, ?, ?)",
            (analysis_type, json.dumps(data, default=str), irq_score),
        )
        analysis_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return analysis_id

    def get_analysis_history(self, analysis_type: str = None, limit: int = 20) -> List[Dict]:
        """Obtém histórico de análises"""
        conn = self._get_connection()
        cursor = conn.cursor()

        if analysis_type:
            cursor.execute(
                "SELECT * FROM analysis_history WHERE analysis_type = ? ORDER BY created_at DESC LIMIT ?",
                (analysis_type, limit),
            )
        else:
            cursor.execute(
                "SELECT * FROM analysis_history ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )

        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            item = dict(row)
            try:
                item["data"] = json.loads(item["data_json"])
            except (json.JSONDecodeError, KeyError):
                item["data"] = {}
            results.append(item)
        return results

    # ─────────────────────────────────────────
    # SNAPSHOTS DE MERCADO
    # ─────────────────────────────────────────

    def save_market_snapshot(self, asset: str, price: float, volume: float = 0, change_24h: float = 0):
        """Salva snapshot de preço do mercado"""
        conn = self._get_connection()
        conn.execute(
            "INSERT INTO market_snapshots (asset, price, volume, change_24h) VALUES (?, ?, ?, ?)",
            (asset, price, volume, change_24h),
        )
        conn.commit()
        conn.close()

    def get_price_history(self, asset: str, limit: int = 100) -> List[Dict]:
        """Obtém histórico de preços salvos"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM market_snapshots WHERE asset = ? ORDER BY captured_at DESC LIMIT ?",
            (asset, limit),
        )
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_stats(self) -> Dict:
        """Retorna estatísticas gerais do banco"""
        conn = self._get_connection()
        cursor = conn.cursor()

        stats = {}
        for table in ["portfolios", "positions", "trades", "analysis_history", "market_snapshots"]:
            cursor.execute(f"SELECT COUNT(*) as count FROM {table}")
            stats[table] = cursor.fetchone()["count"]

        conn.close()
        return stats


# Instância global
try:
    db = Database()
except Exception as e:
    print(f"[Database] Erro ao inicializar: {e}")
    db = None
