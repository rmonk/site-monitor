FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    PORT=8000 \
    HOST=0.0.0.0

WORKDIR /app

# Install system dependencies required by Chromium / Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libasound2t64 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages & Playwright Chromium browser
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install chromium

# Copy application files
COPY app/ ./app/

# Expose container port & setup volume for SQLite persistence
EXPOSE 8000
VOLUME ["/data"]

# Run application with Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
