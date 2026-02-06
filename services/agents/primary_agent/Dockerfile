# Use Python 3.11 slim image for smaller size and security
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip first
RUN pip install --no-cache-dir --upgrade pip

# Copy requirements file and install dependencies BEFORE SDK (changes less frequently)
COPY ./services/agents/primary_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy and install VOS SDK last (changes more frequently)
COPY ./sdk /tmp/sdk
RUN pip install --no-cache-dir /tmp/sdk

# Copy tools directory (needed for standard tools)
COPY ./services/tools /app/tools

# Copy application code (includes system_prompt.txt which is read from /app/system_prompt.txt)
COPY ./services/agents/primary_agent .

# Clean up SDK directory to reduce image size (must be done as root)
RUN rm -rf /tmp/sdk

# Create non-root user for security
RUN adduser --disabled-password --gecos '' --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

# Run the application
CMD ["python", "main.py"]