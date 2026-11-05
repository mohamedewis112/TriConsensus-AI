FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    TRADING_MODE=paper \
    V6_MARL_OBSERVE=true \
    V6_MARL_ENABLED=false \
    V6_MARL_SHADOW_MODE=true

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r /app/backend/requirements.txt "websockets>=15,<16" \
 && if [ "$(uname -m)" = "x86_64" ]; then \
      pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch>=2.9,<3"; \
    else \
      pip install --no-cache-dir "torch>=2.9,<3"; \
    fi

COPY backend /app/backend

CMD ["python", "-m", "triconsensus.v6_microstructure_worker"]
