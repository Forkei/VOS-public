FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY services/scheduler_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY services/scheduler_service/app/ ./app/

# Set Python path
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Run the scheduler service
CMD ["python", "app/main.py"]
