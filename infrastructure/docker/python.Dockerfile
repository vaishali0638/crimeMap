# Python services (API and worker). Build context: repository root.
#   docker build -f infrastructure/docker/python.Dockerfile --target api .
#   docker build -f infrastructure/docker/python.Dockerfile --target worker .
FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --system --gid 10001 crimemap && useradd --system --uid 10001 --gid crimemap --no-create-home crimemap
WORKDIR /app
COPY services/api/pyproject.toml services/api/alembic.ini /app/api/
COPY services/api/src /app/api/src
COPY services/api/migrations /app/api/migrations
RUN pip install /app/api

FROM base AS api
WORKDIR /app/api
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "crimemap_api.main:build", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]

FROM base AS worker
COPY services/worker/pyproject.toml /app/worker/
COPY services/worker/src /app/worker/src
RUN pip install /app/worker && install -d -o 10001 -g 10001 -m 700 /batches
USER 10001:10001
CMD ["dramatiq", "crimemap_worker.jobs", "--processes", "1", "--threads", "2"]
