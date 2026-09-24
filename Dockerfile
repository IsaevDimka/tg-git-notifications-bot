FROM python:3.12-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /srv
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DATA_DIR=/data

COPY --from=build /opt/venv /opt/venv
WORKDIR /srv
COPY app ./app

COPY entrypoint.sh /entrypoint.sh
RUN groupadd --system --gid 10001 bot && useradd --system --uid 10001 --gid 10001 bot \
    && mkdir -p /data && chown bot:bot /data
VOLUME /data

HEALTHCHECK --interval=60s --timeout=5s --start-period=90s CMD ["python", "-m", "app.health"]
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "app"]
