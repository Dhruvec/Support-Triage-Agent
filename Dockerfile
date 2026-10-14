# syntax=docker/dockerfile:1
#
# Hardened production image for the triage agent.
#   * Stage 1 builds wheels (compilers live only here, never in the runtime image)
#   * Stage 2 is a slim, non-root runtime with no dev dependencies
#
# Build:  docker build -t triage-agent .
# Run:    docker run -p 8000:8000 --env-file .env triage-agent
#
# The same image serves both the API (default CMD) and the arq worker
# (override CMD with: arq apps.worker.main.WorkerSettings).

# ---------------------------------------------------------------------------
# Stage 1: build wheels
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# psycopg[binary] / numpy / scikit-learn ship prebuilt wheels, but a compiler
# is kept here so any sdist fallback still builds without polluting the runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY agent ./agent
COPY apps ./apps
COPY mcp_servers ./mcp_servers
COPY evals ./evals

RUN pip install --upgrade pip \
    && pip wheel --wheel-dir /wheels .

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is kept for the container healthcheck (see HEALTHCHECK below).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --create-home app

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --upgrade pip \
    && pip install --no-index --find-links=/wheels triage-agent \
    && rm -rf /wheels

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
