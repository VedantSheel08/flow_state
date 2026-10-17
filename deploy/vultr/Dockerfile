# FlowState backend (real SUMO via the eclipse-sumo wheels) + experiment API.
# NOT BUILT OR RUN while this was written (no Docker on the development machine): see README.md "Status".
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 FLOWSTATE_BACKEND=libsumo
WORKDIR /app

# eclipse-sumo / libsumo ship manylinux wheels (the SUMO binaries are inside the wheel): no apt packages needed for SUMO itself.
COPY backend/requirements.txt backend/requirements-integrations.txt /app/backend/
RUN pip install -r /app/backend/requirements.txt -r /app/backend/requirements-integrations.txt

COPY backend /app/backend
COPY data/networks /app/data/networks
ENV PYTHONPATH=/app/backend

# Secrets are never baked into the image: DATABASE URL / API keys come from the runtime environment (docker compose env_file).
RUN useradd --create-home flowstate && mkdir -p /app/data/work /app/data/runs /app/data/experiments /app/data/benchmarks && chown -R flowstate /app/data
USER flowstate
EXPOSE 8000 8010
# default: the live simulator API; the compose file runs the experiment API from the same image
CMD ["python", "-m", "flowstate.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
