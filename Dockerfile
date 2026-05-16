# syntax=docker/dockerfile:1.7
#
# Multi-stage build:
#   1. `builder` stage installs all Python deps into a vendored `/install`
#      directory using a slim base + temporary build tools (gcc, libffi-dev,
#      libssl-dev) needed by a handful of wheels with C extensions
#      (cryptography, bcrypt, asyncpg, pillow, http-ece, pywebpush).
#   2. `runtime` stage starts from the same slim base, copies only the
#      installed packages, and ships without compilers or build headers.
#
# Net result: runtime image trims build tooling and exposes a smaller surface
# for CVE scanners. The image still runs as the non-root `bloobcat` UID 10001
# the prod docker-compose expects, the `/app/logs` volume mount works
# unchanged, and `ENTRYPOINT` is preserved.

ARG PYTHON_VERSION=3.12-slim

# ── Stage 1: build dependencies ─────────────────────────────────────────────
FROM python:${PYTHON_VERSION} AS builder

WORKDIR /build

# Build-only tooling for C-extension wheels. `--no-install-recommends` keeps
# the layer minimal even though it gets discarded with this whole stage.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip in its own layer so subsequent runs hit the cache.
RUN pip install --no-cache-dir --upgrade pip

# Install deps into /install so we can `COPY` them into the runtime stage
# without dragging in `/usr/local/lib` system files from the build base.
COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION} AS runtime

WORKDIR /app

ARG BUILD_TIME=""
ENV BLOOBCAT_BUILD_TIME=${BUILD_TIME} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy ONLY the installed packages from the builder stage. This skips the
# build-essential cruft and apt cache the build stage accumulated.
COPY --from=builder /install /usr/local

# Non-root user (UID 10001 matches docker-compose volume ownership).
RUN addgroup --system --gid 10001 bloobcat \
    && adduser --system --uid 10001 --ingroup bloobcat --home /app --no-create-home bloobcat \
    && mkdir -p /app/logs \
    && chown -R bloobcat:bloobcat /app

COPY --chown=bloobcat:bloobcat . .

USER bloobcat

ENTRYPOINT [ "python", "-m", "bloobcat" ]
