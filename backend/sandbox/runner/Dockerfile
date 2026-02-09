FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir \
    numpy==2.2.2 \
    pandas==2.2.3 \
    matplotlib==3.10.0

WORKDIR /workspace
CMD ["sleep", "infinity"]
