FROM python:3.12-slim

# Install UV
RUN pip install uv --no-cache-dir

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Copy source packages
COPY src/ ./src/
COPY backend/ ./backend/

# Install all project dependencies via UV
RUN uv sync --no-dev

# Ensure output directories exist
RUN mkdir -p /app/output/charts

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
