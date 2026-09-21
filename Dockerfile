# ---- Dashboard build ----
# The dashboard is compiled to static files and served by the API itself, so
# the browser talks to one origin: api.js can keep its relative "/api" base,
# there is no CORS to configure, and the SSE stream can carry the API token in
# a header. The production stack no longer runs a Vite dev server.
FROM node:20-alpine AS dashboard
WORKDIR /dash
COPY web/dashboard/package.json web/dashboard/package-lock.json ./
# `ci`, not `install`: it installs exactly the lockfile, so the image cannot
# silently pick up a different dependency tree than the one that was tested.
RUN npm ci --no-audit --no-fund
COPY web/dashboard/ ./
RUN npm run build

# Multi-stage build for smaller final image
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# ---- Final stage ----
FROM python:3.11-slim

WORKDIR /app

# Install FFmpeg, OpenCV dependencies, and Node.js (for yt-dlp JS challenges)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libgles2 \
    libegl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual env from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Always upgrade yt-dlp to latest (YouTube bot-detection changes frequently)
RUN pip install --upgrade --no-cache-dir yt-dlp

# Copy application code
COPY . .

# The built dashboard. web/api/app.py mounts this directory at "/" when it
# exists, and falls back to a JSON hint when it does not.
COPY --from=dashboard /dash/dist /app/web/dashboard/dist

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Create required directories
# data/ holds the API token and the saved settings, both 0600.
RUN mkdir -p /app/uploads /app/outputs /app/custom_fonts /app/data /tmp/Ultralytics
RUN chown -R appuser:appuser /app /tmp/Ultralytics
# docker-compose overrides the runtime user to match the host's uid (see the
# `user:` key there), and that uid is not appuser. Anything written outside the
# bind mounts therefore has to be writable by an arbitrary uid.
RUN chmod 777 /tmp/Ultralytics

# Switch to non-root user
USER appuser

# Expose FastAPI port
EXPOSE 8000

# Run FastAPI app
CMD ["uvicorn", "web.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
