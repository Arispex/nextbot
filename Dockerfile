# syntax=docker/dockerfile:1.7

# ---------- Stage 1: build the virtualenv with uv ----------
FROM python:3.11-slim-bookworm AS builder

# Pull a pinned uv binary; pin the version for reproducible builds.
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency manifests first so the layer is cached when only source changes.
COPY pyproject.toml uv.lock ./

# Build a self-contained venv at /app/.venv with locked dependencies.
# BuildKit cache mount keeps uv's wheel cache across builds.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


# ---------- Stage 2: runtime ----------
FROM python:3.11-slim-bookworm AS runtime

# Reuse the venv built above.
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXTBOT_DATA_DIR=/app/data \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install Chromium plus its system libraries via Playwright.
# `--with-deps` invokes apt-get under the hood; clean lists + cache afterwards.
# Maintainer note: any future apt-get additions MUST use
# `--no-install-recommends` and clean both /var/lib/apt/lists/* and
# /var/cache/apt/archives/* in the same RUN.
RUN /app/.venv/bin/playwright install --with-deps chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb

WORKDIR /app

# Project source last so iterative code changes only invalidate this layer.
COPY . .

# Prepare the data directory; bind-mounted from the host in production.
RUN mkdir -p /app/data

# Create an unprivileged runtime user and transfer ownership of writable paths.
# playwright cache (/ms-playwright) was populated above as root; chown it so
# the unprivileged user can launch the browser.
RUN useradd -r -u 1000 -m -d /home/nextbot -s /bin/bash nextbot \
    && chown -R nextbot:nextbot /app /home/nextbot /ms-playwright

USER nextbot

EXPOSE 18081

CMD ["python", "bot.py"]
