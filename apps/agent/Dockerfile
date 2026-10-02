# The voice-agent worker, deployed to LiveKit Cloud with `lk agent deploy` (see DEPLOY.md).
# The build context is apps/agent. LiveKit injects LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET
# and the secrets set with `lk agent update-secrets` (GEMINI_API_KEY) as environment variables.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY agent.py ./
COPY interview ./interview

RUN useradd --system --uid 10001 --no-create-home agent && chown -R agent:agent /app
USER agent

# Download the model files the framework needs (e.g. voice-activity detection) at build time.
RUN python agent.py download-files || true

CMD ["python", "agent.py", "start"]
