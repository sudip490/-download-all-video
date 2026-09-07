FROM python:3.12-slim

# ffmpeg merges video + audio, converts audio, embeds subtitles and thumbnails.
# Deno is the JavaScript runtime yt-dlp needs to solve YouTube's player challenges.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates \
    && curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -o /tmp/deno.zip \
    && unzip -q /tmp/deno.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/deno \
    && rm -f /tmp/deno.zip \
    && apt-get purge -y unzip \
    && rm -rf /var/lib/apt/lists/* \
    && deno --version

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
