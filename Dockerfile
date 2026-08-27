# syntax=docker/dockerfile:1.4
# Bot di trading Hyperliquid (paper) — immagine runtime minimale.
FROM python:3.12-slim
WORKDIR /app

COPY pyproject.toml ./
COPY tradingagents ./tradingagents
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 120 --retries 5 -e .

# state/ (SQLite + heartbeat) resta un volume montato per sopravvivere ai rebuild.
VOLUME /app/state

CMD ["python", "-m", "tradingagents.hyperliquid.loop"]
