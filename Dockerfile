FROM python:3.12-slim

# Install system dependencies: ffmpeg, curl, ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY bot/ /app/bot/

# Ensure directories exist
RUN mkdir -p /app/downloads /app/data/cookies

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot.main"]
