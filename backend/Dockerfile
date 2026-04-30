# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python dependencies first for layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Application code
COPY app /app/app

# Run as a non-root user
RUN useradd --system --uid 1001 --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# The image has no curl/wget on purpose; use the Python stdlib to probe
# the FastAPI `/health` endpoint after the lifespan startup completes.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=10 \
    CMD python -c "import json,sys,urllib.request; \
import urllib.error; \
sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)).get('status')=='ok' else 1)" \
    || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
