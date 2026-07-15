FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

LABEL org.opencontainers.image.title="flight-price-tracker" \
      org.opencontainers.image.description="Generic flight price alert bot" \
      org.opencontainers.image.source="https://github.com/Yisuskr/flight-price-tracker"

WORKDIR /app

COPY --from=builder /install /usr/local
COPY tracker/ tracker/
COPY config.yaml .

VOLUME ["/app/data"]

RUN adduser --disabled-password --gecos "" tracker && \
    chown -R tracker:tracker /app
USER tracker

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
  CMD python -c "from tracker.config import load_config; print('OK')" || exit 1

ENTRYPOINT ["python", "-m", "tracker.main"]
