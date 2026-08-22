ARG PYTHON_IMAGE=python:3.11-slim
FROM ${PYTHON_IMAGE}

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TWIPSYBOT_UP_MODE=foreground

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv export --frozen --no-emit-project -o requirements.lock.txt && \
    uv pip install --system --no-build --require-hashes -r requirements.lock.txt

COPY twipsybot /app/twipsybot
COPY plugins /app/plugins
RUN uv build --wheel -o dist && \
    uv pip install --system --no-build --no-deps dist/*.whl && \
    useradd -r -u 10001 -m -U -s /usr/sbin/nologin appuser && \
    mkdir -p /app/logs /app/data && \
    chown -R appuser:appuser /app/logs /app/data

USER appuser

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD pid=$(cat data/twipsybot.pid 2>/dev/null) && test -n "$pid" && test -e "/proc/$pid"

ENTRYPOINT ["twipsybot"]
CMD ["up"]
