FROM python:3.11-slim

WORKDIR /app

# Install system deps for asyncpg (PostgreSQL client)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy dependency definition first (for Docker layer caching)
COPY pyproject.toml .

# Install Python dependencies
RUN pip install --no-cache-dir -e ".[dev]"

# Copy application code
COPY . .

CMD ["python", "run.py"]
