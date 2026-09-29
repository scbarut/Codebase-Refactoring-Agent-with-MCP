# Multi-version Python Sandbox Dockerfile for safe test execution
# Supports Python 3.9, 3.10, 3.11, 3.12 via build-arg
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

# Environment settings to avoid interactive prompts and optimize python in container
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace

# Install system dependencies needed for common Python native builds and git
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Pre-install pytest and coverage as required by spec
RUN pip install --no-cache-dir pytest pytest-cov coverage

# Default entrypoint runs pytest on the mounted /workspace directory
CMD ["pytest"]
