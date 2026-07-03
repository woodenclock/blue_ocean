FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml .python-version ./

# Install dependencies (no project install — we copy source separately)
RUN uv sync --no-dev --no-install-project

# Copy application source and default data
COPY app/ ./app/
COPY data/ ./data/

# Runtime environment
ENV DATA_DIR=/app/data
ENV PYTHONUNBUFFERED=1

EXPOSE 8008

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8008"]
