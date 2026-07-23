# syntax=docker/dockerfile:1.14

ARG PYTHON_IMAGE=python:3.14-slim-bookworm@sha256:86f975aca15cf04a40b399eebede9aea7c82eae084d1f1a0a6ef6bcaae871a30
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.29@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc
ARG DOCKER_CLI_IMAGE=docker:29.1.3-cli@sha256:4fa0ee1f3a7e4354c4ea34558b6d4ee32859baf4973d4c8ccc8e7fe3dd730c04

FROM ${UV_IMAGE} AS uv
FROM ${DOCKER_CLI_IMAGE} AS docker-cli

FROM ${PYTHON_IMAGE} AS browser-base
ARG CHROMIUM_VERSION=150.0.7871.181-1~deb12u1
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHROME_BIN=/usr/bin/chromium
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        "chromium=${CHROMIUM_VERSION}" \
        ca-certificates \
        libnss3-tools \
    && rm -rf /var/lib/apt/lists/*

FROM browser-base AS toolchain
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /workspace

FROM toolchain AS build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY MANIFEST.in THIRD_PARTY_NOTICES.md CHANGELOG.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --out-dir /dist \
    && mkdir -p /opt/cdpx/site-packages \
    && uv pip install --system --target /opt/cdpx/site-packages /dist/*.whl
COPY packaging/native-python packaging/native-chromium packaging/native-certutil \
    packaging/native-cdpx packaging/embedded-install /opt/cdpx/bin/
RUN chmod 0755 /opt/cdpx/bin/* \
    && mkdir -p /opt/cdpx/root/usr/lib /opt/cdpx/root/etc /opt/cdpx/chromium-libs \
    && cp -a /usr/lib/chromium /opt/cdpx/root/usr/lib/ \
    && cp -a /usr/local /opt/cdpx/root/usr/ \
    && cp -a /usr/share /opt/cdpx/root/usr/ \
    && cp -a /etc/ssl /opt/cdpx/root/etc/ \
    && cp -a /etc/fonts /opt/cdpx/root/etc/ \
    && cp -L /usr/bin/certutil /opt/cdpx/bin/certutil.bin \
    && { \
        ldd /usr/lib/chromium/chromium; \
        ldd /usr/lib/chromium/chrome_crashpad_handler; \
        ldd /usr/lib/*-linux-gnu/libsoftokn3.so; \
        ldd /usr/lib/*-linux-gnu/libfreeblpriv3.so; \
        ldd /usr/bin/certutil; \
    } \
        | awk '$2 == "=>" && $3 ~ /^\// {print $3} $1 ~ /^\// {print $1}' \
        | sort -u \
        | while read -r library; do \
            name="$(basename "$library")"; \
            case "$name" in \
                libc.so.6|libdl.so.2|libm.so.6|libpthread.so.0|libresolv.so.2|librt.so.1|libutil.so.1|ld-linux-*.so.*) ;; \
                *) cp -L "$library" "/opt/cdpx/chromium-libs/$name" ;; \
            esac; \
        done \
    && for library in \
        /usr/lib/*-linux-gnu/libfreebl3.so \
        /usr/lib/*-linux-gnu/libfreeblpriv3.so \
        /usr/lib/*-linux-gnu/libnssckbi.so \
        /usr/lib/*-linux-gnu/libnssdbm3.so \
        /usr/lib/*-linux-gnu/libsoftokn3.so; do \
            cp -L "$library" /opt/cdpx/chromium-libs/; \
        done \
    && ln -s bin/embedded-install /opt/cdpx/install

FROM toolchain AS dev
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        git \
        make \
        shellcheck \
    && rm -rf /var/lib/apt/lists/*
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins \
    /usr/local/libexec/docker/cli-plugins
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY tools ./tools
COPY scripts ./scripts
COPY tests ./tests
COPY docs ./docs
COPY site ./site
COPY schemas ./schemas
COPY packaging ./packaging
COPY .github ./.github
COPY AGENTS.md CLAUDE.md HARNESS.md CHANGELOG.md CONTRIBUTING.md CODE_OF_CONDUCT.md ./
COPY SECURITY.md SUPPORT.md THIRD_PARTY_NOTICES.md MANIFEST.in ./
COPY Dockerfile docker-bake.hcl Makefile dev cdpx ./
COPY docker-compose.symfony-e2e.yml ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
ENV PATH="/workspace/.venv/bin:${PATH}" \
    PYTHONPATH="/workspace/src:/workspace" \
    CDPX_CONTAINERIZED=1

FROM dev AS ci
CMD ["python", "-m", "tools.harness", "check"]

FROM ${PYTHON_IMAGE} AS runtime
ARG VERSION=0.1.0
ARG REVISION=uncommitted
LABEL org.opencontainers.image.title="cdpx" \
      org.opencontainers.image.description="Supervised Chrome DevTools primitives for coding agents" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="https://github.com/inem0o/cdpx"
COPY --from=build /opt/cdpx /opt/cdpx
RUN ln -s /opt/cdpx/bin/native-cdpx /usr/local/bin/cdpx
ENV CDPX_BUNDLED_CHROME=/opt/cdpx/bin/native-chromium \
    CDPX_CERTUTIL=/opt/cdpx/bin/native-certutil \
    CDPX_CONTAINERIZED=1 \
    PYTHONPATH=/opt/cdpx/site-packages
WORKDIR /workspace
ENTRYPOINT ["python", "-m", "cdpx.runtime", "guardian"]
CMD ["--idle-timeout", "86400"]

FROM scratch AS embedded
COPY --from=build /opt/cdpx /opt/cdpx
