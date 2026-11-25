# Build context is the repository root: docker build -f services/dealer/Dockerfile .
FROM node:24-slim AS site

WORKDIR /site

COPY services/dealer/site/package.json services/dealer/site/package-lock.json ./
RUN npm ci

COPY services/dealer/site ./
RUN npm run build

FROM python:3.12.16-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/srv/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /srv

RUN useradd --system --create-home app

# The path dependency is two folders up from the project, as it is in the repository.
COPY doors/pyproject.toml doors/uv.lock /srv/doors/
COPY doors/src /srv/doors/src

WORKDIR /srv/services/dealer
COPY services/dealer/pyproject.toml services/dealer/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/dealer/app ./app
RUN uv sync --frozen --no-dev

# Everything here belongs to root, so the service can read it and not change it.
COPY --from=site /site/dist /srv/site

USER app
EXPOSE 8000
# An open event stream must not hold up a stop.
CMD ["/srv/.venv/bin/uvicorn", "--factory", "app.main:build_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--timeout-graceful-shutdown", "5"]
