# All-in-one image for Glama (and any container-based MCP host): boots the
# TypeScript MCP server over stdio, which auto-spawns the Python engine as a
# child process inside the same container (spawn.ts finds it via
# GROUNDCHECK_ENGINE_DIR). docker-compose.yml still builds engine/Dockerfile
# for engine-only deployments.
#
# The free-llm-router Python twin is vendored in engine/vendor (committed),
# so this builds from a bare checkout with no pre-build step.

FROM node:22-slim AS server-build
WORKDIR /build
COPY server/package.json server/package-lock.json ./
RUN npm ci
COPY server/tsconfig.json ./
COPY server/src ./src
RUN npm run build && npm prune --omit=dev

FROM python:3.12-slim
WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY engine/requirements.txt engine/requirements.txt
RUN pip install --no-cache-dir -r engine/requirements.txt

COPY engine/groundcheck_engine engine/groundcheck_engine
COPY engine/vendor engine/vendor

COPY --from=server-build /build/dist server/dist
COPY --from=server-build /build/node_modules server/node_modules
COPY server/package.json server/package.json

ENV GROUNDCHECK_ENGINE_DIR=/app/engine \
    GROUNDCHECK_ROUTER_PATH=/app/engine/vendor \
    GROUNDCHECK_PYTHON=python3

# Without a provider key (GROQ_API_KEY / CEREBRAS_API_KEY / OPENROUTER_API_KEY)
# the tools still list and run; every verdict degrades honestly to "unverified".
CMD ["node", "server/dist/server.js"]
