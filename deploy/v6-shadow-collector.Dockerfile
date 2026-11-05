FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    TRADING_MODE=paper \
    V6_MARL_OBSERVE=true \
    V6_MARL_ENABLED=false \
    V6_MARL_SHADOW_MODE=true

WORKDIR /app

COPY backend/requirements-v6-collector.txt /app/backend/requirements-v6-collector.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r /app/backend/requirements-v6-collector.txt

COPY backend /app/backend

CMD ["python", "-m", "triconsensus.v6_shadow_collector"]
