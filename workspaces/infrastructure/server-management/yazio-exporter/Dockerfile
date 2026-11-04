FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir "psycopg[binary]==3.2.3" "paho-mqtt==2.1.0"

WORKDIR /app
COPY exporter.py /app/exporter.py
COPY weight_bridge.py /app/weight_bridge.py
COPY import_openscale_csv.py /app/import_openscale_csv.py

ENTRYPOINT ["python", "/app/exporter.py"]
