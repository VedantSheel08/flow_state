# FlowState on Render (free web service): one container serves the built frontend, the REST API and /ws
# from the same origin. Build from the repo root; Render's default Dockerfile path is ./Dockerfile.

# --- frontend build ---
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

# --- API + SUMO (pip wheel ships the binaries) ---
FROM python:3.12-slim
# Free instances have 512 MB RAM: keep glibc from keeping one malloc arena per thread.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/backend MALLOC_ARENA_MAX=2 \
    FLOWSTATE_BACKEND=traci FLOWSTATE_RUNS_DIR=/app/data/runs FLOWSTATE_STATIC_DIR=/app/frontend/dist
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY data/networks data/networks
COPY --from=web /web/dist frontend/dist
# Free-tier instances have no persistent disk, so saved runs and caches live in the container filesystem
# and reset on redeploy or restart.
RUN mkdir -p /app/data/runs /app/data/work
EXPOSE 8000
# One worker only: run/controller state lives in process memory. Render injects PORT.
CMD ["sh", "-c", "exec python -m uvicorn flowstate.api.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
