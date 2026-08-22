# Bot di trading Hyperliquid (paper) — immagine runtime minimale.
FROM python:3.12-slim
WORKDIR /app

COPY pyproject.toml ./
COPY tradingagents ./tradingagents
RUN pip install --no-cache-dir -e .

# state/ (SQLite + heartbeat) resta un volume montato per sopravvivere ai rebuild.
VOLUME /app/state

CMD ["python", "-m", "tradingagents.hyperliquid.loop"]
