# Minimal image for PDF Renamer Bot
FROM python:3.11-slim

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
ENV ONEDRIVE_FOLDER_PATH="/data/input" \
    OUTPUT_BASE_FOLDER="" \
    CLASSIFICATION_RULES_FILE="/app/classification_rules.yaml" \
    MAX_FILENAME_LENGTH=100

# Create mount points for host folders
RUN mkdir -p /data/input /data/output

CMD ["python", "pdf_renamer_bot.py"]
