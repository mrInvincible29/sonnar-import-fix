# Multi-platform Dockerfile for Sonarr Import Monitor
# Supports both ARM64 (M1/M2 Mac, Raspberry Pi) and AMD64 (Intel/AMD)

# Build arguments for multi-platform support
ARG BUILDPLATFORM
ARG TARGETPLATFORM
ARG PYTHON_VERSION=3.11

# Build stage - compile dependencies
FROM --platform=$BUILDPLATFORM python:${PYTHON_VERSION}-slim AS builder

# Build info
ARG BUILDPLATFORM
ARG TARGETPLATFORM
RUN echo "Building on $BUILDPLATFORM for $TARGETPLATFORM"

# Set working directory for build
WORKDIR /build

# Install build dependencies for Python packages that need compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies globally in builder stage
RUN pip install --no-cache-dir -r requirements.txt

# Verify installation
RUN python -c "import requests, yaml, flask; print('Dependencies installed successfully')"

# Runtime stage - minimal production image
FROM python:${PYTHON_VERSION}-slim AS runtime

# Metadata
LABEL maintainer="Sonarr Import Monitor Team"
LABEL version="2.0.0"
LABEL description="Automatically fixes Sonarr import scoring issues"
LABEL org.opencontainers.image.source="https://github.com/yourusername/sonarr-import-monitor"

# Install only essential runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 -d /app -s /bin/false appuser

# Set working directory
WORKDIR /app

# Copy Python packages from builder stage (global site-packages)
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code with proper ownership
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser main.py .
COPY --chown=appuser:appuser requirements.txt .

# Create directories for logs and config with proper permissions
RUN mkdir -p /app/logs /app/config \
    && chown -R appuser:appuser /app/logs /app/config

# Switch to non-root user
USER appuser

# Ensure Python can find packages
ENV PATH=/usr/local/bin:$PATH

# Environment variables with secure defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    LOG_LEVEL=INFO \
    LOG_FORMAT=json \
    WEBHOOK_PORT=8090 \
    WEBHOOK_HOST=0.0.0.0 \
    MONITORING_INTERVAL=60

# Health check - verify the application is running and responsive
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${WEBHOOK_PORT}/health || exit 1

# Expose webhook port
EXPOSE 8090

# Create entrypoint script for flexible startup
COPY --chown=appuser:appuser docker/entrypoint.sh /entrypoint.sh

# Use entrypoint script
ENTRYPOINT ["/entrypoint.sh"]

# Default command - can be overridden
CMD ["--webhook"]