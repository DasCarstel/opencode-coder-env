# Hestia — OpenCode Model Panel (Container)
# Baut Backend + committed Frontend-Dist ein; im Container wird nichts gebaut.
FROM python:3.12-slim

WORKDIR /app

COPY backend/*.py /app/
COPY frontend/dist /app/static

# Postgres-Treiber für das Wardrobe-Panel (render_usage/panel_settings).
RUN pip install --no-cache-dir psycopg2-binary

ENV HESTIA_STATIC=/app/static \
    HESTIA_PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python3", "bootstrap.py"]
