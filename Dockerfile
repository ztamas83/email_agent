FROM python:3.12-slim

# Install uv binary from official image for fast package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Set working directory
WORKDIR /app

# Ensure immediate terminal logging
ENV PYTHONUNBUFFERED=1

# Install project dependencies
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# Copy application files
COPY . .

# Run the daemon
CMD ["python", "daemon.py"]
