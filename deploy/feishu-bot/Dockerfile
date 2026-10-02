FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 \
    DCA_BOT_DATA_DIR=/app/data HOME=/tmp
WORKDIR /app
COPY requirements-dca-reminder.txt requirements-feishu-bot.txt ./
RUN pip install --no-cache-dir -r requirements-feishu-bot.txt \
    && useradd --uid 10001 --create-home bot \
    && mkdir /app/data && chown bot:bot /app/data
COPY scripts ./scripts
COPY .github/dca-reminder.json ./.github/dca-reminder.json
COPY .github/dca-reminder-state.json ./.github/dca-reminder-state.json
USER bot
HEALTHCHECK --interval=60s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import pathlib,time; p=pathlib.Path('/app/data/heartbeat'); assert p.exists() and time.time()-float(p.read_text()) < 1800"
CMD ["python", "-m", "scripts.feishu_bot"]
