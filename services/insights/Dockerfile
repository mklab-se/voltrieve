# Build context is the repository root: docker build -f services/insights/Dockerfile .
FROM python:3.12.16-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /srv

RUN useradd --system --create-home app

# Path dependencies sit beside the project, as they do in the repository.
COPY search/pyproject.toml search/uv.lock /srv/search/
COPY search/src /srv/search/src
COPY agent_core/pyproject.toml agent_core/uv.lock /srv/agent_core/
COPY agent_core/src /srv/agent_core/src

WORKDIR /srv/services/insights
COPY services/insights/pyproject.toml services/insights/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/insights/src ./src
RUN uv sync --frozen --no-dev && chown -R app:app /srv

USER app
# One run, then it ends. An argument, such as --window-end=2026-11-22, goes to the job.
ENTRYPOINT ["/srv/services/insights/.venv/bin/python", "-m", "voltrieve_insights.job"]
