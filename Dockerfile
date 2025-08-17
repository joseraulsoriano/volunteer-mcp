FROM python:3.13-slim

WORKDIR /app

# Instalar dependencias del sistema para lxml (compilación) y limpiar cache apt
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Copiar todo el repo al contenedor
COPY . /app

# Dependencias de Python
RUN pip install --no-cache-dir fastapi uvicorn gunicorn aiohttp beautifulsoup4 lxml prometheus-client redis python-dotenv

ENV PORT=8080

# Ejecutar desde el directorio del servicio para imports relativos
WORKDIR /app/mcp/volunteer

EXPOSE 8080

# Usar $PORT provisto por Railway
CMD ["sh", "-c", "gunicorn mcp_api:app -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:${PORT:-8080}"]


