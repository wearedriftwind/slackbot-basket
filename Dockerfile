FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABAS_SOKVAG=/data/biljetter.db \
    CSV_IMPORT_SOKVAG=/app/docs/lulea_basket_hemmamatcher_2026-27.csv

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY biljettbot/ biljettbot/
COPY docs/lulea_basket_hemmamatcher_2026-27.csv docs/

# SQLite-databasen ska ligga på en volym monterad på /data så att den överlever
# omstarter och omdeployer (Railway: Volume på /data; Compose: se docker-compose.yml).
# Ingen VOLUME-instruktion här – Railway tillåter den inte.

CMD ["python", "-m", "biljettbot"]
