# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies (cached layer)
COPY pyproject.toml requirements.txt ./
RUN uv pip install --system -r requirements.txt

# Copy source
COPY *.py ./

# Data directory — mount config.toml and state.json here,
# and the .session file will be written here on first auth.
VOLUME ["/data"]

# Keep stdin open so Pyrogram can prompt for phone/OTP on first run.
# After auth the .session file persists in /data across restarts.
ENV PYTHONUNBUFFERED=1

# Run from /data so that forwarder.session is written there and persists.
WORKDIR /data
ENTRYPOINT ["python", "/app/main.py", "/data/config.toml"]
