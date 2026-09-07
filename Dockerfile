FROM python:3.12-slim

# ffmpeg merges video + audio, converts audio, embeds subtitles and thumbnails
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates ./templates

# Safe defaults for a server on the internet. Set APP_PASSWORD when you run it.
ENV PUBLIC_MODE=1 \
    HOST=0.0.0.0 \
    PORT=5000 \
    PYTHONUNBUFFERED=1

EXPOSE 5000
CMD ["python", "app.py"]
