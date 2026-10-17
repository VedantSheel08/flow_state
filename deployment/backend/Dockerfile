# FlowState simulation backend: FastAPI + SUMO (pip wheel ships the binaries).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend FLOWSTATE_BACKEND=traci FLOWSTATE_RUNS_DIR=/var/lib/flowstate/runs
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY data data
RUN useradd -m flowstate && mkdir -p /var/lib/flowstate/runs /app/data/work && chown -R flowstate /var/lib/flowstate /app/data
USER flowstate
EXPOSE 8000
# One worker only: run/controller state lives in process memory.
CMD ["python", "-m", "uvicorn", "flowstate.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
