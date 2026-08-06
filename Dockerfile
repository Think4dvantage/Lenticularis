FROM python:3.11-slim

WORKDIR /app

# Install system-level build deps for pyproj and other C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libproj-dev \
    proj-data \
    proj-bin \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip/setuptools first — the base image's bundled pip has hit a known upstream
# bug (crashes computing its User-Agent when setuptools' version metadata is unreadable),
# reproducing specifically under linux/arm64 QEMU emulation when poetry later shells out
# to pip to swap in a platform-specific cryptography wheel.
RUN pip install --no-cache-dir --upgrade pip setuptools

# Install Poetry
RUN pip install --no-cache-dir poetry==2.1.1

# Copy dependency manifests first (layer caching). Literal filename, not a glob —
# a missing poetry.lock must fail the build loudly, not silently fall through.
COPY pyproject.toml poetry.lock ./

# Install runtime dependencies only (no dev tools in prod image)
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi --no-root

# Copy application source
COPY src/ ./src/
COPY static/ ./static/
COPY config.yml.example ./config.yml.example

# Create data and log directories
RUN mkdir -p /app/data /app/logs

ENV PYTHONPATH=/app/src
ENV CONFIG_PATH=/app/config.yml

EXPOSE 8000

CMD ["uvicorn", "lenticularis.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
