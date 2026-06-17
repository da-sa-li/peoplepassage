FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Nicht-Root-User für den Laufzeitprozess.
RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Datenverzeichnis für die SQLite-DB; gehört dem app-User, damit das benannte
# Volume beim ersten Anlegen die Rechte erbt.
RUN mkdir -p /data && chown -R app:app /app /data
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
