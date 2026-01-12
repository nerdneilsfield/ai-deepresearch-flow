FROM ghcr.io/astral-sh/uv:python3.12-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

COPY . /app

RUN uv pip install --system .

ENTRYPOINT ["deepresearch-flow"]
CMD ["--help"]
