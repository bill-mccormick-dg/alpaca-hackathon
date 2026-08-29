# Local dev / experiment image. NOT the production path - CT 108 runs the bot
# from a venv under cron (see README "Operations"); this image exists so a
# teammate can build, test, and run dry-run cycles with one command, and so
# N challenger configs can run in parallel (issue #13).
FROM python:3.12-slim

# alpaca-mcp-server is launched as a subprocess by bot/alpaca_mcp.py; it is
# a pip-installed console script, so it lands in the same interpreter's bin.
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Never write __pycache__ into a bind-mounted checkout; run as a non-root
# user so anything it writes into ./logs is not root-owned on Linux hosts.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 1000 bot && mkdir -p /app/logs && chown -R bot:bot /app
USER bot

ENTRYPOINT ["python"]
CMD ["run_cycle.py", "--dry-run", "--force"]
