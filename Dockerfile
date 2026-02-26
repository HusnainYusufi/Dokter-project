# =============================================================================
# Stage 1 — Builder
# Install all Python dependencies into an isolated venv.
# Build tools (gcc/g++) stay here and never reach the final image.
# =============================================================================
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

# gcc + g++ are required by some transitive deps (e.g. SQLAlchemy Cython ext)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy only the dependency manifest first → Docker layer-cache friendly.
# Rebuilding the image after a code-only change skips this expensive step.
COPY requirements.txt .

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


# =============================================================================
# Stage 2 — Runtime
# Lean final image: no compiler, no build artifacts, non-root user.
# =============================================================================
FROM python:3.11-slim-bookworm AS runtime

# --- Security: run as a non-root user ----------------------------------------
RUN groupadd -r appuser \
    && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# Copy the pre-built venv from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy application source code
# WORKDIR is /app, so "app/static" resolves to /app/app/static — correct.
COPY app/ ./app/

# Hand all files to the non-root user before switching
RUN chown -R appuser:appuser /app

# --- Environment -------------------------------------------------------------
ENV PATH="/opt/venv/bin:$PATH" \
    # Don't buffer stdout/stderr — logs appear immediately in `docker logs`
    PYTHONUNBUFFERED=1 \
    # Don't write .pyc bytecode files inside the container
    PYTHONDONTWRITEBYTECODE=1

# --- Runtime user ------------------------------------------------------------
USER appuser

# --- Networking --------------------------------------------------------------
EXPOSE 8000

# --- Health check ------------------------------------------------------------
# Pings the OpenAPI JSON endpoint; fails fast if the app hasn't started yet.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/openapi.json')" \
        || exit 1

# --- Start -------------------------------------------------------------------
# No --reload in production. Scale horizontally via container replicas instead.
# Pass LLAMA_CLOUD_API_KEY at runtime:
#   docker run -e LLAMA_CLOUD_API_KEY=<key> ...
#   docker run --env-file .env ...
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info"]
