# Production image: the FastAPI service AND the built web app in one container (one origin, so the
# session cookie is first-party and there is no CORS). Used by Render (see render.yaml).
#
#   docker build -t speak-your-log .
#   docker run --env-file .env -p 8000:8000 speak-your-log

# ---- stage 1: build the React app ---------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build

# ---- stage 2: the API runtime -------------------------------------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv

COPY apps/api/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY apps/api/app ./app
COPY --from=web /web/dist ./web_dist

# Run as an unprivileged user.
RUN useradd --system --uid 10001 --no-create-home app && chown -R app:app /srv
USER app

ENV WEB_DIST_DIR=/srv/web_dist \
    APP_ENV=production
EXPOSE 8000

# --proxy-headers: behind Render's proxy, trust X-Forwarded-For so request.client is the real
# visitor (the per-IP rate limits depend on it). The container is only reachable through that proxy.
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --no-server-header"]
