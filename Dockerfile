ARG PYTHON_IMAGE=python:3.11-slim
FROM ${PYTHON_IMAGE}

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt ./
RUN python -m pip install -r requirements.txt

COPY misskey_ai /app/misskey_ai
COPY plugins /app/plugins
COPY run.py /app/run.py

RUN useradd -r -u 10001 -m -U -s /usr/sbin/nologin appuser && \
    mkdir -p /app/logs /app/data && \
    chown -R appuser:appuser /app/logs /app/data

USER appuser

CMD ["python", "run.py"]
