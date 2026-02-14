FROM python:3.11-slim

WORKDIR /app

# Install system deps for asyncpg (PostgreSQL client)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy dependency definition first (for Docker layer caching)
COPY pyproject.toml .

# Install Python dependencies (production only, no editable mode)
RUN pip install --no-cache-dir .

# Copy application code
COPY . .

# Run as non-root user
RUN useradd --create-home appuser
USER appuser

CMD ["python", "run.py"]
