FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

# Copy and install dependencies
COPY ./services/agents/calculator_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install VOS SDK
COPY ./sdk /tmp/sdk
RUN pip install --no-cache-dir /tmp/sdk

# Copy tools and agent files (includes system_prompt.txt)
COPY ./services/tools/ /app/tools/
COPY ./services/agents/calculator_agent .

# Run as non-root user
RUN useradd --create-home --shell /bin/bash calculator_user
RUN chown -R calculator_user:calculator_user /app
USER calculator_user

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "calculator_agent"]
