FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CHROME_BIN=chromium

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ca-certificates make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY pyproject.toml README.md ./
COPY LICENSE CHANGELOG.md ./
COPY src ./src
COPY tests ./tests
COPY docs ./docs
COPY Dockerfile .gitlab-ci.yml Makefile HARNESS.md CLAUDE.md ./

RUN pip install -e ".[dev]"

CMD ["make", "test-e2e"]
