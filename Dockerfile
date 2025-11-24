FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-install-project --no-dev

# Copy project files
COPY src/ src/
COPY litellm_config.yaml .

# Install the project without re-resolving dependencies
RUN uv pip install --no-deps .

# Create directory for sqlite db
RUN mkdir -p /data

# Expose the port
EXPOSE 8000

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Command to run the application
CMD ["memori-proxy", "--host", "0.0.0.0", "--port", "8000"]
