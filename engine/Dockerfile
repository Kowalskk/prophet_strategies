FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema necesarias para compilar asyncpg y otros módulos
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Primero, quitamos el comentario de asyncpg si lo habíamos testeado localmente
RUN sed -i 's/# asyncpg/asyncpg/g' requirements.txt

# Instalar los requerimientos
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código fuente
COPY . .

# Exponer el puerto del API
EXPOSE 8000

# Comando para ejecutar el servidor con Uvicorn
CMD ["uvicorn", "prophet.main:app", "--host", "0.0.0.0", "--port", "8000"]
