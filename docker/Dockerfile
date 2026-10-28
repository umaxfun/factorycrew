# Test image: Factorio headless (pinned to the version the player runs) + FactorioTest + Python.
# Build for linux/amd64: on Apple Silicon it runs under Rosetta. The arm64 variant of the
# factoriotools image runs the x64 binary through box64 and a direct exec fails.
ARG FACTORIO_VERSION=2.0.77
FROM --platform=linux/amd64 factoriotools/factorio:${FACTORIO_VERSION} AS factorio

# factorio-test from the mod portal needs a Factorio login, so build it from source instead.
# cli-v3.5.1 is the last tag whose mod targets factorio_version 2.0 (mod 3.0.2).
FROM --platform=linux/amd64 node:22-slim AS ftbuild
ARG FT_TAG=cli-v3.5.1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 -b "${FT_TAG}" https://github.com/GlassBricks/FactorioTest /ft \
    && cd /ft && git submodule update --init --depth 1 \
    && npm ci && npm run build -w mod
RUN v="$(node -p "require('/ft/mod/info.json').version")" \
    && mkdir -p /out && cp -r /ft/mod "/out/factorio-test_${v}" \
    && rm -rf "/out/factorio-test_${v}/node_modules"

FROM --platform=linux/amd64 node:22-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pytest \
    && rm -rf /var/lib/apt/lists/*
COPY --from=factorio /opt/factorio /opt/factorio
COPY --from=ftbuild /out /opt/factorio-test-mods
# factorio-test-cli 3.5.x shells out to `npx fmtk` (bin of factoriomod-debug) to enable mods.
RUN npm install -g factorio-test-cli@3.5.1 factoriomod-debug@2.1.9
ENV PATH=/opt/factorio/bin/x64:$PATH
WORKDIR /work
