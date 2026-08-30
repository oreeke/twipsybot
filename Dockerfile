ARG PYTHON_IMAGE=python:3.11-slim

FROM ${PYTHON_IMAGE} AS builder

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv export --frozen --no-emit-project -o requirements.lock.txt && \
    uv pip install --system --only-binary :all: --require-hashes -r requirements.lock.txt

COPY twipsybot /app/twipsybot
RUN uv build --wheel -o dist && \
    uv pip install --system --no-build --no-deps --no-index --find-links dist "twipsybot==$(uv version --short)"

FROM ${PYTHON_IMAGE}

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TWIPSYBOT_HOLD_ON_STARTUP_ERROR=1

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/twipsybot /usr/local/bin/twipsybot

COPY plugins /app/plugins
RUN useradd -r -u 10001 -m -U -s /usr/sbin/nologin appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appuser /app/data

USER appuser

ENTRYPOINT ["twipsybot"]
CMD ["run"]
