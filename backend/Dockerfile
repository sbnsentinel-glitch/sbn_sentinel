# SES-010 Compliant Dockerfile
# Stage 1: Build dependencies
FROM python:3.11-slim as builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt

# Stage 2: Production runtime
FROM python:3.11-slim

WORKDIR /app

# Run as non-root user for security (SES-008 & SES-010)
RUN addgroup --system appgroup && adduser --system appuser --ingroup appgroup

COPY --from=builder /app/wheels /wheels
COPY --from=builder /app/requirements.txt .

RUN pip install --no-cache /wheels/*

COPY . .

# Set proper ownership
RUN chown -R appuser:appgroup /app

USER appuser

# Set PYTHONPATH so Python can find the 'app' module inside src/runtime/
ENV PYTHONPATH="src/runtime"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
