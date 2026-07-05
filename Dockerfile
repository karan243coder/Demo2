FROM python:3.11-slim

# Metadata
LABEL maintainer="MeetLink"
LABEL description="MeetLink Backend - Telegram Logger Server"

# Set working directory
WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server code
COPY server.py .
COPY config.py .

# Create temp directory for recordings
RUN mkdir -p /tmp/meetlink_recordings

# Expose port 8080
EXPOSE 8080

# Health check on /api/status
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/status')" || exit 1

# Run the server
CMD ["python", "server.py"]
