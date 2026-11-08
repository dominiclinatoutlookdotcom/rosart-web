# The Rosart publisher image (sgs-server spec 006, research R-12, contracts/rosart-web-interfaces.md R-5).
# Stages: `build` (Python base, Node, locked dependencies, the site and the committed export/), `probe` (the
# V-0 CPU probe, plan Phase 0: `make image-probe`), `awscli` (the pinned AWS CLI v2 zip, verified by SHA-256),
# `final` (the service image: `make image VERSION=vX.Y.Z`) and `test` (the suite inside the image:
# `make image-test`).
#
# Bases are the Harbor frozen/ copies of python:3.14-slim and node:24-slim, pinned by digest (constitution
# VI). Owner task T001 freezes them and reports the two digests; put them in the ARGs below in a reviewed
# commit (64 hex characters, without the `sha256:` prefix). PENDING-T001 is not a valid digest, so
# `docker build` and `make image-probe` refuse to build until then.
ARG NODE_SHA256=c0a93797ee78814ae6d32fcca2561fdbbe423149c3f5389aad32d86e9fd85bd4
ARG PYTHON_SHA256=ac25e5afbfdb4a94ff99cc24b0345719210133144481480bcba6085efa36a341
# AWS CLI v2 for linux x86_64, from AWS's own download host, pinned by version and by the SHA-256 of the zip
# (ADD --checksum refuses any other bytes). A new release is a reviewed bump of both values.
ARG AWSCLI_VERSION=2.39.6
ARG AWSCLI_SHA256=7a121264f2637a3b90767aaf5bdd43ee88b8c925a931d0479fa2ce22b1a2fa76

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

# V-0 probe: on the box, `docker run --rm --network none --read-only --tmpfs /tmp:size=2g,exec --user 10001:10001`
# runs the site build once on the box CPU (i7-3770K, no AVX2) and prints its duration and peak memory.
# The `,exec` matters: Docker mounts --tmpfs noexec by default and the probe builds from /tmp (exit 126).
FROM build AS probe
COPY scripts/probe-build.sh ./scripts/probe-build.sh
USER 10001:10001
CMD ["sh", "/app/scripts/probe-build.sh"]

# The AWS CLI, installed in its own stage so unzip and the zip never reach the service image.
FROM harbor.int.solagratia.com.au/frozen/python@sha256:${PYTHON_SHA256} AS awscli
ARG AWSCLI_VERSION
ARG AWSCLI_SHA256
RUN apt-get update \
 && apt-get install -y --no-install-recommends unzip \
 && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:${AWSCLI_SHA256} https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWSCLI_VERSION}.zip /tmp/awscliv2.zip
RUN unzip -q /tmp/awscliv2.zip -d /tmp \
 && /tmp/aws/install --install-dir /usr/local/aws-cli --bin-dir /usr/local/bin

# The service image. /app (code, node_modules, the committed export/) is owned by root and world-readable:
# the container root file system is read-only and the service runs as uid 10001, so it can never change its
# own code. Everything it writes goes to /data, /export.git and the /tmp tmpfs (plan "Mount table").
FROM build AS final
ARG PUBLISHER_CODE_COMMIT=unknown
COPY --from=awscli /usr/local/aws-cli /usr/local/aws-cli
RUN ln -s /usr/local/aws-cli/v2/current/bin/aws /usr/local/bin/aws \
 && aws --version \
 && node --version | grep -Fq "v24." \
 && python --version | grep -Fq "Python 3.14."

COPY --chmod=u=rwX,go=rX publisher ./publisher
COPY --chmod=u=rwX,go=rX tools ./tools
COPY --chmod=u=rwX,go=rX scripts ./scripts
COPY vitest.config.ts deploy.config.json ./

# No telemetry, no update checks, no pager, no prompts; HOME and the npm cache live on the /tmp tmpfs.
ENV HOME=/tmp/home \
    NPM_CONFIG_CACHE=/tmp/npm \
    AWS_PAGER="" \
    AWS_CLI_AUTO_PROMPT=off \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PUBLISHER_CODE_COMMIT=${PUBLISHER_CODE_COMMIT}

USER 10001:10001
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD ["python", "-m", "publisher", "healthcheck"]
CMD ["python", "-m", "publisher", "serve"]

# The suite inside the image (`make image-test`): POSIX-only tests must run, never skip. Not part of the
# released image; pytest comes from PyPI at build time and is pinned.
FROM final AS test
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends make \
 && rm -rf /var/lib/apt/lists/* \
 && python -m pip install --no-cache-dir pytest==9.1.1
COPY tests ./tests
COPY Makefile Dockerfile .dockerignore README.md CLAUDE.md ./
COPY docs ./docs
COPY .claude/skills ./.claude/skills
ENV PUBLISHER_REQUIRE_POSIX=1
USER 10001:10001
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
