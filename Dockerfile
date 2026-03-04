FROM python:3.11-slim

WORKDIR /app

# Dependências do sistema (necessárias para algumas libs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependências Python (--prefer-binary evita compilação de C no Railway)
COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

# Copiar código
COPY . .

# Criar diretório de dados persistente
RUN mkdir -p /app/data

# Expor porta (Railway injeta $PORT)
EXPOSE 8001

# Iniciar servidor
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8001}"]
