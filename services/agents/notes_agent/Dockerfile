# Notes Agent Service Dockerfile
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip first
RUN pip install --no-cache-dir --upgrade pip

# Copy requirements and install dependencies BEFORE SDK (changes less frequently)
COPY ./services/agents/notes_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy and install VOS SDK last (changes more frequently)
COPY ./sdk /tmp/sdk
RUN pip install --no-cache-dir /tmp/sdk

# Copy tools directory (needed for notes and standard tools)
COPY ./services/tools/ /app/tools/

# Copy the notes agent specific files (includes system_prompt.txt)
COPY ./services/agents/notes_agent .

# Clean up SDK directory to reduce image size (must be done as root)
RUN rm -rf /tmp/sdk

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash notes_user
RUN chown -R notes_user:notes_user /app
USER notes_user

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Run the notes agent
CMD ["python", "main.py"]
