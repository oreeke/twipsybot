FROM ghcr.io/astral-sh/uv:0.13.6 AS uv
FROM python:3.12-alpine AS base

FROM base AS builder

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY --from=uv /uv /uvx /bin/

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv export --frozen --no-emit-project -o requirements.lock.txt && \
    uv pip install --system --only-binary :all: --require-hashes -r requirements.lock.txt

COPY twipsybot /app/twipsybot
RUN uv build --wheel -o dist && \
    uv pip install --system --no-build --no-deps --no-index --find-links dist "twipsybot==$(uv version --short)" && \
    cd /usr/local/lib/python3.12/site-packages && \
    find . -type d \( -name __pycache__ -o -name tests -o -name test \) -prune -exec rm -rf {} + && \
    rm -rf pip pip-* setuptools* wheel* pkg_resources _distutils_hack distutils-precedence.pth

FROM base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    COLORTERM=truecolor

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/twipsybot /usr/local/bin/twipsybot

COPY plugins /app/plugins
RUN adduser -D -u 10001 -s /sbin/nologin appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appuser /app/data

USER appuser

ENTRYPOINT ["twipsybot"]
CMD ["run"]
