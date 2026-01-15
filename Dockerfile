FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY . /app

RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir .

ENTRYPOINT ["deepresearch-flow"]
CMD ["--help"]
