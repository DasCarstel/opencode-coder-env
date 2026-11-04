FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir "psycopg[binary]==3.2.3"

WORKDIR /app
COPY exporter.py /app/exporter.py

ENTRYPOINT ["python", "/app/exporter.py"]
