# Shared image for both the gift agent service and the partner mock server.
# They live in the same codebase and share dependencies; docker-compose selects
# which one to run via the service `command`.
FROM python:3.12-slim

# Keep Python output unbuffered so logs stream promptly to `docker compose logs`.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so the layer is cached across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code.
COPY gift_agent ./gift_agent
COPY partner_mock ./partner_mock
COPY crypto ./crypto
COPY prompts ./prompts
COPY user_profile.json ./user_profile.json

# Default command is overridden per-service in docker-compose.yml.
CMD ["python", "-m", "partner_mock.server"]
