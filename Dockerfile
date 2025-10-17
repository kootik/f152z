# Dockerfile
ARG PYTHON_VERSION=3.11

# ===== Stage 1: Dependencies builder =====
FROM python:${PYTHON_VERSION}-slim AS builder

# Set build arguments
ARG BUILD_DATE
ARG VCS_REF
ARG VERSION=1.0.0

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.source="https://github.com/kootik/f152z"

WORKDIR /app

# Copy and install Python dependencies
# Обратите внимание: requirements-dev.txt* - это wildcard, который может вызвать проблемы.
# Мы будем копировать файлы явно.
COPY requirements.txt requirements-dev.txt ./
RUN python -m pip install --upgrade pip setuptools wheel && \
    pip wheel --no-cache-dir --wheel-dir /app/wheels -r requirements.txt

# ===== Stage 2: Test runner =====
FROM python:${PYTHON_VERSION}-slim AS tester

# Install test dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy wheels from builder
COPY --from=builder /app/wheels /wheels
COPY requirements-dev.txt ./

# Install main and test dependencies
RUN pip install --no-cache /wheels/* && \
    pip install --no-cache-dir -r requirements-dev.txt

# Copy application code
COPY . .

# Run tests (этот шаг будет выполняться в CI, а не при локальной сборке)
# RUN python -m pytest tests/ -v --cov=app --cov-report=term-missing --cov-report=xml

# ===== Stage 3: Security scanner =====
FROM python:${PYTHON_VERSION}-slim AS security

RUN pip install --no-cache-dir safety bandit

WORKDIR /app
COPY requirements.txt ./

# Security checks
RUN safety check --file requirements.txt --json || true
RUN bandit -r app/ -f json -o bandit-report.json || true

# ===== Stage 4: Final production image =====
FROM python:${PYTHON_VERSION}-slim AS production

# Metadata
LABEL maintainer="your-email@example.com" \
      org.label-schema.build-date=$BUILD_DATE \
      org.label-schema.vcs-ref=$VCS_REF \
      org.label-schema.version=$VERSION \
      org.label-schema.schema-version="1.0"

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    tini \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

WORKDIR /app

# Copy wheels and install
COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache /wheels/* && \
    rm -rf /wheels

# Create non-root user
RUN groupadd -r appuser && \
    useradd -r -g appuser -u 1000 -m -s /bin/bash appuser

# Copy application with correct permissions
COPY --chown=appuser:appuser . .

# Create necessary directories
RUN mkdir -p /app/logs && \
    chown -R appuser:appuser /app/logs

# Security: Remove unnecessary files
RUN find /app -name "*.pyc" -delete && \
    find /app -name "__pycache__" -type d -delete && \
    rm -rf /app/tests /app/.git /app/.pytest_cache /app/requirements-dev.txt

# Switch to non-root user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Use tini as init system
ENTRYPOINT ["/usr/bin/tini", "--"]

# Run application
CMD ["gunicorn", "--worker-class", "eventlet", "-w", "4", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info", "wsgi:app"]

EXPOSE 8000
