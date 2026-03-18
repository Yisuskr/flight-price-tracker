# ─── Build stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install dependencies into a dedicated layer for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─── Runtime stage ───────────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL org.opencontainers.image.title="flight-price-tracker" \
      org.opencontainers.image.description="Miami → Tenerife flight price alert bot" \
      org.opencontainers.image.source="https://github.com/YOUR_USERNAME/flight-price-tracker"

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source code
COPY tracker/ tracker/
COPY config.yaml .

# Persistent volume for the SQLite database
VOLUME ["/app/data"]

# Run as non-root for security
RUN adduser --disabled-password --gecos "" tracker && \
    chown -R tracker:tracker /app
USER tracker

# Health: just verify Python and imports work
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
  CMD python -c "from tracker.config import load_config; print('OK')" || exit 1

ENTRYPOINT ["python", "-m", "tracker.main"]
