# syntax=docker/dockerfile:1
# Base images are pinned to their multi-arch index digests so amd64 and arm64
# always build from the same upstream revision. Override the whole reference to
# use a mirror, e.g. --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
ARG PYTHON_BASE_IMAGE=python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
ARG NODE_BASE_IMAGE=node:24-alpine@sha256:50c8e8ca1d27439048670df5883f32d57cf81cff6233222c893fd0d9884cbd81

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
# requirements.lock is generated from requirements.txt (see AGENTS.md); hash
# checking guarantees both architectures install the exact same versions.
COPY requirements.lock ./requirements.lock
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install --require-hashes -r requirements.lock

FROM ${PYTHON_BASE_IMAGE} AS runtime

RUN groupadd -g 1001 appgroup && \
    useradd -u 1001 -g appgroup -s /bin/bash -m appuser

WORKDIR /app
RUN mkdir images data && \
    chown -R appuser:appgroup images data
COPY --from=python-builder --chown=appuser:appgroup /install /usr/local
# Least-volatile layers first: the frontend bundle only changes with frontend
# work, backend/ with backend work, and VERSION on every release.
COPY --from=frontend-builder --chown=appuser:appgroup /frontend/build ./frontend/build
COPY --chown=appuser:appgroup backend/ ./backend/
COPY --chown=appuser:appgroup VERSION .

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

# Declared last so a changing version/revision only touches image metadata and
# never invalidates the filesystem layers above.
ARG APP_VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.source="https://github.com/Z1rconium/gpt-image-linux" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

CMD ["granian", "backend.app.main:app"]
