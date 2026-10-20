# tapo-web in a container. How to run it: docs/DOCKER.md
FROM python:3.12-slim

# ffmpeg (and ffprobe) does the live view, the recordings and the SD clip muxing.
# tzdata lets the app use the camera's own time zone for the SD card calendar.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    TAPO_WEB_HOST=0.0.0.0 \
    TAPO_CONFIG_DIR=/config \
    TAPO_DATA_DIR=/data

RUN useradd --create-home --uid 1000 tapo

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Live HLS segments and logs are written next to the app, settings and videos go to the volumes.
RUN mkdir -p /app/hls /app/logs /config /data \
    && chown tapo:tapo /app/hls /app/logs /config /data

USER tapo

VOLUME ["/config", "/data"]
EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/health', timeout=4)"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
