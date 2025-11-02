# Dockerfile para Django no Fly.io com MySQL
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# deps de sistema necessárias pro mysqlclient e futuramente postgres
RUN apt-get update && apt-get install -y \
    build-essential \
    pkg-config \
    default-libmysqlclient-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# instalar deps python
COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && \
    pip install -r /tmp/requirements.txt && \
    rm -rf /root/.cache

# copiar o projeto inteiro
COPY . /app

# coletar estáticos (ajusta se seu manage.py não estiver na raiz)
RUN python manage.py collectstatic --noinput

EXPOSE 8000

# nome do seu projeto django parece ser "app" (pelo aviso do fly)
CMD ["gunicorn", "app.wsgi:application", "--bind", "0.0.0.0:8000"]
