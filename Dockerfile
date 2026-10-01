# The battle-companion web app (`vgc web`) for Fly.io. See deploy/README.md.
#
# Built from the local working tree, not from git: the served models, the replay cache and the
# set corpus are gitignored and exist only where they were made. `.dockerignore` keeps the
# training data (snapshots, features, self-play: about a gigabyte) out of the context.

# ---- stage 1: the frontend, built into src/vgc/web/static -----------------------------------
FROM node:24-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- stage 2: the pinned Showdown and the calc sidecar, built for Linux -----------------------
# Built from the submodule's source rather than copied: the local node_modules hold macOS builds
# of SQLite modules. --ignore-scripts skips those native builds; the simulator never loads them.
FROM node:24-bookworm-slim AS node
WORKDIR /app/vendor/pokemon-showdown
COPY vendor/pokemon-showdown/ ./
RUN npm ci --ignore-scripts && node build
WORKDIR /app/sidecar/calc
COPY sidecar/calc/package.json sidecar/calc/package-lock.json ./
RUN npm ci --ignore-scripts

# ---- stage 3: the runtime: Python, with node for the solver and the simulator -----------------
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VGC_ROOT=/app \
    PORT=8080
WORKDIR /app

COPY --from=node /usr/local/bin/node /usr/local/bin/node

COPY pyproject.toml README.md ./
COPY src/ ./src/
# Editable, so the package runs from /app/src: the app serves the frontend from its own directory.
RUN pip install --no-cache-dir -e ".[web]"

COPY --from=frontend /app/src/vgc/web/static ./src/vgc/web/static
COPY --from=node /app/vendor/pokemon-showdown ./vendor/pokemon-showdown
COPY --from=node /app/sidecar/calc/node_modules ./sidecar/calc/node_modules
COPY sidecar/ ./sidecar/
COPY configs/ ./configs/
COPY models/ ./models/
COPY data/regulations/ ./data/regulations/
COPY data/teams/ ./data/teams/
COPY data/splits/ ./data/splits/
COPY data/replays/ ./data/replays/

# What the app writes lives on the volume at /data: your teams, your battles, and the solver's
# cache (.vgc). The links dangle until the volume is mounted; the entrypoint makes their targets.
RUN ln -s /data/library ./data/library && ln -s /data/battles ./data/battles && ln -s /data/vgc ./.vgc
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["entrypoint.sh"]
CMD ["sh", "-c", "vgc web --host 0.0.0.0 --port ${PORT:-8080}"]
