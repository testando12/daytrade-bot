#!/bin/bash
# Script de setup e inicialização do Day Trade Bot

echo "🤖 Iniciando setup do Day Trade Bot..."

# Criar ambiente virtual
echo "1. Criando ambiente virtual..."
python -m venv venv

# Ativar ambiente (Windows)
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    source venv/Scripts/activate
else
    source venv/bin/activate
fi

# Instalar dependências
echo "2. Instalando dependências..."
pip install -r requirements.txt

# Criar arquivo .env
if [ ! -f .env ]; then
    echo "3. Criando arquivo .env..."
    cp .env.example .env
    echo "✅ Arquivo .env criado. Configure com seus dados."
fi

# Inicializar banco de dados (comentado para não auto-criar)
# echo "4. Inicializando banco de dados..."
# python init_db.py

echo ""
echo "✅ Setup concluído!"
echo ""
echo "Para iniciar o bot:"
echo "  1. Configure o arquivo .env se necessário"
echo "  2. Execute: python -m uvicorn app.main:app --reload --port 8000"
echo ""
echo "Para testar os engines:"
echo "  python test_engines.py"
echo ""
echo "Para fazer backtesting:"
echo "  python backtest.py"
