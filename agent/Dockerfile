FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install system dependencies required for build and audio/network libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY alembic.ini ./alembic.ini
COPY alembic/ ./alembic/

# Expose ports: 8000 (FastAPI Token Service) and 8081 (LiveKit Agent health)
EXPOSE 8000 8081

# Default command runs the LiveKit agent worker
CMD ["python", "app/agent_entrypoint.py", "start"]
