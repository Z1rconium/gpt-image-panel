# syntax=docker/dockerfile:1
ARG PYTHON_BASE_IMAGE=python:3.11-slim
ARG NODE_BASE_IMAGE=node:24-alpine

FROM --platform=$BUILDPLATFORM ${NODE_BASE_IMAGE} AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci
COPY frontend/svelte.config.js frontend/vite.config.ts frontend/tsconfig.json frontend/tailwind.config.ts frontend/postcss.config.cjs ./
COPY frontend/static/ ./static/
COPY frontend/src/ ./src/
RUN --mount=type=cache,target=/root/.npm \
    --mount=type=cache,target=/frontend/node_modules/.vite \
    npx svelte-kit sync && npm run build

FROM ${PYTHON_BASE_IMAGE} AS python-builder
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install -r requirements.txt

FROM ${PYTHON_BASE_IMAGE} AS runtime

LABEL org.opencontainers.image.source="https://github.com/Z1rconium/gpt-image-linux"

RUN groupadd -g 1001 appgroup && \
    useradd -u 1001 -g appgroup -s /bin/bash -m appuser

WORKDIR /app
RUN mkdir images data && \
    chown -R appuser:appgroup images data
COPY --from=python-builder --chown=appuser:appgroup /install /usr/local
COPY --chown=appuser:appgroup VERSION .
COPY --chown=appuser:appgroup backend/ ./backend/
COPY --from=frontend-builder --chown=appuser:appgroup /frontend/build ./frontend/build

EXPOSE 9090

ENV GRANIAN_INTERFACE=asgi \
    GRANIAN_HOST=0.0.0.0 \
    GRANIAN_PORT=9090 \
    GRANIAN_LOOP=uvloop \
    GRANIAN_RUNTIME_THREADS=2 \
    GRANIAN_RUNTIME_MODE=auto \
    GRANIAN_WORKERS=1 \
    GRANIAN_BACKPRESSURE=100 \
    GRANIAN_BACKLOG=2048 \
    GRANIAN_STATIC_PATH_ROUTE=/_app/immutable \
    GRANIAN_STATIC_PATH_MOUNT=/app/frontend/build/_app/immutable \
    GRANIAN_STATIC_PATH_EXPIRES=31536000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9090/health')" || exit 1

CMD ["granian", "backend.app.main:app"]
