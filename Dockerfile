# Multi-stage build for FastMCP Docker Context Manager
FROM python:3.11-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:$PATH"

# Set working directory for build
WORKDIR /build

# Copy project files
COPY pyproject.toml uv.lock ./
COPY README.md ./
COPY docker_mcp/ ./docker_mcp/

# Install dependencies with uv sync
RUN uv sync --frozen --no-dev

# Production stage
FROM python:3.11-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    # Docker CLI for remote Docker management
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    # SSH client for remote host connections
    openssh-client \
    # Additional utilities
    git \
    && rm -rf /var/lib/apt/lists/*

# Add Docker's official GPG key and repository
RUN mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
    $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker CLI
RUN apt-get update && apt-get install -y docker-ce-cli && \
    rm -rf /var/lib/apt/lists/*

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

# Create directories for SSH keys and config (will be mounted)
RUN mkdir -p /home/dockermcp/.ssh && \
    chown -R dockermcp:dockermcp /home/dockermcp/.ssh && \
    chmod 700 /home/dockermcp/.ssh

# Create directory for application data
RUN mkdir -p /app/data && \
    chown -R dockermcp:dockermcp /app/data

# Switch to non-root user
USER dockermcp

# Set environment variables
ENV FASTMCP_HOST=0.0.0.0
ENV FASTMCP_PORT=8000
ENV LOG_LEVEL=INFO
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8000

# Entry point
ENTRYPOINT ["/opt/venv/bin/python", "-m", "docker_mcp.server"]