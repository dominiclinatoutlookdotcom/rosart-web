# The Rosart publisher image (sgs-server spec 006, research R-12, contracts/rosart-web-interfaces.md R-5).
# Stages so far: `build` (Python base, Node, locked dependencies, the site and the committed export/) and
# `probe` (the V-0 CPU probe, plan Phase 0: `make image-probe`). The final and test stages come with T098.
#
# Bases are the Harbor frozen/ copies of python:3.14-slim and node:24-slim, pinned by digest (constitution
# VI). Owner task T001 freezes them and reports the two digests; put them in the ARGs below in a reviewed
# commit (64 hex characters, without the `sha256:` prefix). PENDING-T001 is not a valid digest, so
# `docker build` and `make image-probe` refuse to build until then.
ARG NODE_SHA256=01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9
ARG PYTHON_SHA256=af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d

FROM harbor.int.solagratia.com.au/frozen/node@sha256:${NODE_SHA256} AS node

FROM harbor.int.solagratia.com.au/frozen/python@sha256:${PYTHON_SHA256} AS build

# git, ca-certificates and tzdata are the image's Debian packages (R-12); libstdc++6 is the one runtime
# library the Node binary needs that a slim base may lack.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git tzdata libstdc++6 \
 && rm -rf /var/lib/apt/lists/*

# Node 24 (binary and npm) from the frozen node image, never a download.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && node --version && npm --version

# Builds stay offline: no Astro telemetry, no npm update check, audit or funding calls.
ENV ASTRO_TELEMETRY_DISABLED=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false

WORKDIR /app

# Dependencies exactly as locked (the lockfile records the Linux x64 sharp and lightningcss builds).
COPY package.json package-lock.json .npmrc ./
RUN npm ci --cache /tmp/npm-cache && rm -rf /tmp/npm-cache

# The site and the committed export. export/theme/ (Shopify-licensed reference code) never enters the
# image: only these export paths are copied, and .dockerignore drops it from the context as well.
COPY astro.config.ts tsconfig.json ./
COPY src ./src
COPY public ./public
COPY export/shopify ./export/shopify
COPY export/images ./export/images
COPY export/theme-content.json ./export/theme-content.json

# V-0 probe: on the box, `docker run --rm --network none --read-only --tmpfs /tmp:size=2g --user 10001:10001`
# runs the site build once on the box CPU (i7-3770K, no AVX2) and prints its duration and peak memory.
FROM build AS probe
COPY scripts/probe-build.sh ./scripts/probe-build.sh
USER 10001:10001
CMD ["sh", "/app/scripts/probe-build.sh"]
