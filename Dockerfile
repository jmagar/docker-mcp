# Multi-stage build for FastMCP Docker Context Manager
FROM python:3.11-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Set working directory for build
WORKDIR /build

# Copy only dependency files first (better caching)
COPY pyproject.toml uv.lock README.md ./

# Install dependencies without installing the project itself
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code after dependencies (better layer caching)
COPY docker_mcp/ ./docker_mcp/

# Now install the project itself
RUN uv sync --frozen --no-dev

# Production stage
FROM python:3.11-slim

# Install all runtime dependencies in one layer
RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    openssh-client \
    git \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
    $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y docker-ce-cli \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd --gid 1000 dockermcp && \
    useradd --uid 1000 --gid dockermcp --shell /bin/bash --create-home dockermcp

# Copy Python virtual environment from builder stage
COPY --from=builder /build/.venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy application code
COPY docker_mcp/ ./docker_mcp/
COPY config/ ./config/

# Create directories and set permissions in one layer
RUN mkdir -p /home/dockermcp/.ssh /app/data && \
    chown -R dockermcp:dockermcp /home/dockermcp/.ssh /app/data /app && \
    chmod 700 /home/dockermcp/.ssh

# Switch to non-root user
USER dockermcp

# Set environment variables
ENV FASTMCP_HOST=0.0.0.0 \
    FASTMCP_PORT=8000 \
    LOG_LEVEL=INFO \
    DOCKER_CONTAINER=true \
    PYTHONPATH=/app

# Expose port
EXPOSE 8000

# Entry point
ENTRYPOINT ["/opt/venv/bin/python", "-m", "docker_mcp.server"]