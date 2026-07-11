FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CHROME_BIN=chromium

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ca-certificates make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY pyproject.toml README.md ./
COPY LICENSE CHANGELOG.md CONTRIBUTING.md SECURITY.md CODE_OF_CONDUCT.md SUPPORT.md ./
COPY MANIFEST.in ./
COPY .gitignore .dockerignore ./
COPY src ./src
COPY scripts ./scripts
COPY tests ./tests
COPY docs ./docs
COPY .github ./.github
COPY Dockerfile Makefile HARNESS.md CLAUDE.md ./

RUN pip install -e ".[dev]"

CMD ["make", "test-e2e"]
