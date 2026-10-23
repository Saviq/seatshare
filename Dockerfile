FROM ghcr.io/astral-sh/uv:0.13.5 AS uv
FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY server ./server
COPY web ./web
RUN useradd --system --uid 10001 app && mkdir /data && chown app:app /data
USER app
ENV PATH="/app/.venv/bin:$PATH" DB_PATH=/data/app.db APP_ENV=production COOKIE_SECURE=true
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"]
CMD ["uvicorn", "server.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
