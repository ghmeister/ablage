# Minimal image for PDF Renamer Bot
FROM python:3.11-slim

ARG APP_VERSION=dev
ARG GIT_COMMIT=unknown
ENV APP_VERSION=${APP_VERSION}
ENV GIT_COMMIT=${GIT_COMMIT}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install runtime dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Default environment variables (override in compose/Portainer)
ENV OUTPUT_BASE_FOLDER="" \
    CLASSIFICATION_RULES_FILE="/app/classification_rules.yaml" \
    MAX_FILENAME_LENGTH=100

# /data is the mount point for the persistent token cache volume.
# The MSAL token cache (.token_cache.json) is written here so it
# survives container restarts and image upgrades.
RUN mkdir -p /data && \
    groupadd -r appuser && \
    useradd -r -g appuser -u 1001 appuser && \
    chown -R appuser:appuser /app /data
ENV TOKEN_CACHE_PATH=/data/.token_cache.json

USER appuser

CMD ["python", "pdf_renamer_bot.py"]
