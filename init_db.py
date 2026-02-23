"""
Script para inicializar o banco de dados
Cria as tabelas necessárias
"""

from app.models.database import Base, Portfolio, Position, Trade, MarketData, Analysis
from app.core.database import engine


def init_db():
    """Cria todas as tabelas no banco de dados"""
    print("Criando tabelas do banco de dados...")

    try:
        Base.metadata.create_all(bind=engine)
        print("✅ Tabelas criadas com sucesso!")
        print("\nTabelas criadas:")
        print("  - portfolios")
        print("  - positions")
        print("  - trades")
        print("  - market_data")
        print("  - analysis")

    except Exception as e:
        print(f"❌ Erro ao criar tabelas: {str(e)}")
        raise


def drop_all_tables():
    """Remove todas as tabelas (cuidado!)"""
    print("Removendo todas as tabelas...")

    try:
        Base.metadata.drop_all(bind=engine)
        print("✅ Tabelas removidas com sucesso!")

    except Exception as e:
        print(f"❌ Erro ao remover tabelas: {str(e)}")
        raise


def reset_db():
    """Reseta o banco de dados (remove e recria)"""
    print("Resetando banco de dados...")
    drop_all_tables()
    init_db()
    print("✅ Banco de dados resetado!")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "reset":
            reset_db()
        elif sys.argv[1] == "drop":
            drop_all_tables()
        else:
            init_db()
    else:
        init_db()
