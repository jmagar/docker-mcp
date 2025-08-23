# Docker Manager MCP

[![Docker Image](https://img.shields.io/badge/docker-ghcr.io%2Fjmagar%2Fdocker--mcp-blue)](https://github.com/jmagar/docker-mcp/pkgs/container/docker-mcp)
[![Build Status](https://github.com/jmagar/docker-mcp/actions/workflows/docker-build.yml/badge.svg)](https://github.com/jmagar/docker-mcp/actions/workflows/docker-build.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![FastMCP](https://img.shields.io/badge/FastMCP-2.11.3%2B-green)](https://github.com/jamesturk/fastmcp)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A powerful MCP (Model Context Protocol) server that enables AI assistants to manage Docker containers and stacks across multiple remote hosts. Built with FastMCP's HTTP streaming transport, it provides real-time container monitoring, log streaming, and multi-host Docker orchestration through a secure, production-ready interface.

🐳 **Now with full Docker containerization support!** Deploy in seconds with our one-line installer.

## 📚 Table of Contents

- [✨ Key Features](#-key-features)
- [🏗️ Architecture](#️-architecture)
- [🚀 Quick Start](#-quick-start)
- [🐳 Docker Usage](#-docker-usage)
- [📖 Usage Examples](#-usage-examples)
- [🛠️ Available Tools](#️-available-tools)
- [⚙️ Configuration](#️-configuration)
- [🔒 Security](#-security)
- [💻 Development](#-development)
- [📊 Logging](#-logging)
- [🔍 Troubleshooting](#-troubleshooting)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)
- [🗺️ Roadmap](#️-roadmap)

## 🎯 Quick Install

```bash
# One-line installer with automatic SSH key setup
curl -sSL https://raw.githubusercontent.com/jmagar/docker-mcp/main/install.sh | bash
```

The installer handles everything:
- ✅ Prerequisites check
- ✅ SSH key generation  
- ✅ Host configuration from SSH config
- ✅ Key distribution to Docker hosts
- ✅ Service startup

**Service will be available at:** `http://localhost:8000`

## ✨ Key Features

### Core Functionality
- **Multi-Host Docker Management**: Connect to multiple remote Docker hosts via Docker contexts
- **Consolidated Tool Architecture**: 3 action-based tools instead of 13 individual tools for streamlined operations
- **Real-time Log Streaming**: Stream container and stack logs in real-time using HTTP streaming
- **Stack Deployment**: Deploy Docker Compose stacks with persistent file management
- **Container Lifecycle Management**: Complete container operations (start, stop, restart, build, pull, logs)
- **Port Management & Conflict Detection**: List all port mappings and automatically detect conflicts
- **Docker Context Integration**: Native Docker context support with automatic connection management

### Powerful Capabilities
- **🌐 Multi-Host Control**: Manage containers across unlimited Docker hosts from a single interface
- **📦 Stack Deployment**: Deploy complex Docker Compose applications with one command
- **📊 Real-time Monitoring**: Stream logs and stats from containers in real-time
- **🔍 Port Conflict Detection**: Automatically detect and report port conflicts across hosts
- **🔄 Bulk Operations**: Start/stop/restart multiple containers or entire stacks at once
- **🎯 Smart Host Discovery**: Auto-import hosts from your SSH config with zero configuration
- **📝 Persistent Stack Management**: Deploy once, manage forever - compose files are stored on remote hosts
- **🚀 Zero-Downtime Updates**: Pull and update container images without service interruption

## 🏗️ Architecture

```
┌─────────────────┐    HTTP/SSE    ┌──────────────────┐  Docker Context  ┌─────────────────┐
│  MCP Client     │◄──────────────►│ FastMCP Server   │◄─────────────────►│ Remote Docker   │
│                 │                │                  │                  │ Host 1          │
└─────────────────┘                │                  │                  └─────────────────┘
                                   │                  │  Docker Context  ┌─────────────────┐
                                   │                  │◄─────────────────►│ Remote Docker   │
                                   │                  │                  │ Host 2          │
                                   └──────────────────┘                  └─────────────────┘
```

The server follows a layered architecture with consolidated tools and comprehensive middleware:

### Architecture Layers
- **Consolidated Tools Layer**: 3 consolidated MCP tools (`docker_hosts`, `docker_container`, `docker_compose`) with action-based routing
- **Services Layer**: Business logic, validation, orchestration, and ToolResult formatting
- **Core Layer**: Docker contexts, SSH connections, configuration management, and file watching
- **Middleware Pipeline**: Request processing pipeline with error handling, rate limiting, timing, and dual logging

### Middleware Pipeline (executed in order)
1. **Error Handling Middleware**: Catches all exceptions and provides structured error responses
2. **Rate Limiting Middleware**: Protects against abuse with configurable request limits
3. **Timing Middleware**: Monitors performance and logs slow requests  
4. **Logging Middleware**: Dual logging system (console + persistent files)

### Service Layer Pattern
```
MCP Tools → Services → Core Components → Docker/SSH
     ↓         ↓              ↓            ↓
Validation → Business → Docker Context → Remote Host
Parameter   → Logic   → Management    → Operations
Routing     → Format  → Config Load  → Command Exec
```

### Consolidated Tool Architecture
- **docker_hosts**: 5 host operations (list, add, ports, compose_path, import_ssh)
- **docker_container**: 7 container operations (list, info, start/stop/restart/build/pull, logs)
- **docker_compose**: 8 stack operations (list, deploy, up/down/restart/build/pull, logs, discover)

## 🚀 Quick Start

### Step 1: Installation

#### Option A: Docker Installation (Recommended)

**One-Line Installer:**

```bash
curl -sSL https://raw.githubusercontent.com/jmagar/docker-mcp/main/install.sh | bash
```

This installer will:
- ✅ Check prerequisites (Docker, docker-compose)
- ✅ Generate dedicated ed25519 SSH keys for the container
- ✅ Parse your `~/.ssh/config` and set up host configurations
- ✅ Distribute SSH keys to your Docker hosts (with your approval)
- ✅ Start the Docker MCP service on port 8000

**Manual Docker Setup:**

```bash
# Create directory structure
mkdir -p ~/.docker-mcp/{ssh,config,data}

# Generate SSH keys for Docker MCP
ssh-keygen -t ed25519 -f ~/.docker-mcp/ssh/docker-mcp-key -N ""

# Download docker-compose.yaml
curl -sSL https://raw.githubusercontent.com/jmagar/docker-mcp/main/docker-compose.yaml \
  -o ~/.docker-mcp/docker-compose.yaml

# Download example configuration
curl -sSL https://raw.githubusercontent.com/jmagar/docker-mcp/main/config/hosts.example.yml \
  -o ~/.docker-mcp/config/hosts.yml

# Edit configuration to add your hosts
nano ~/.docker-mcp/config/hosts.yml

# Start services
cd ~/.docker-mcp && docker-compose up -d
```

#### Option B: Local Installation

```bash
# Clone the repository
git clone https://github.com/jmagar/docker-mcp
cd docker-mcp

# Install with uv (recommended)
uv sync

# Alternative: Install with pip
pip install -e .
```

### Step 2: Configuration

The server supports multiple configuration methods with automatic Docker context discovery:

#### Option 1: YAML Configuration (Recommended)

Create `config/hosts.yml`:
```yaml
hosts:
  production-1:
    hostname: 192.168.1.10
    user: dockeruser
    identity_file: ~/.ssh/docker_host_key
    description: "Production Docker host"
    tags: ["production", "web"]
    compose_path: "/mnt/user/compose"  # Path for persistent compose files
    
  staging-1:
    hostname: 192.168.1.20  
    user: dockeruser
    identity_file: ~/.ssh/docker_host_key
    description: "Staging environment"
    tags: ["staging"]
    compose_path: "/home/docker/compose"
    
  development:
    hostname: localhost
    user: ${USER}
    description: "Local development"
    tags: ["development", "local"]


# Server configuration is done via .env file, not in hosts.yml
# See .env.example for server settings (FASTMCP_HOST, FASTMCP_PORT, LOG_LEVEL)
```

#### Option 2: Docker Context Integration

The server automatically uses existing Docker contexts. Create contexts with:
```bash
# Create Docker context for remote host
docker context create production-docker \
  --docker "host=ssh://dockeruser@192.168.1.10" \
  --description "Production Docker host"

docker context create staging-docker \
  --docker "host=ssh://dockeruser@192.168.1.20" \
  --description "Staging Docker host"

# List available contexts
docker context ls
```

#### Option 3: Environment Variables

```bash
# .env file
DOCKER_HOSTS_CONFIG=config/hosts.yml  # Path to hosts configuration
FASTMCP_HOST=127.0.0.1                # Default: 127.0.0.1, use 0.0.0.0 for external access
FASTMCP_PORT=8000                     # Default: 8000
LOG_LEVEL=INFO                        # Default: INFO
```

### Step 3: Docker Context Setup

```bash
# Create Docker context for remote host (Docker handles SSH automatically)
docker context create remote-prod \
  --docker "host=ssh://user@remote-host" \
  --description "Production Docker host"

# Test Docker context connection
docker --context remote-prod ps

# Set up SSH keys (if needed for Docker context)
ssh-keygen -t ed25519 -f ~/.ssh/docker_host_key
ssh-copy-id -i ~/.ssh/docker_host_key.pub user@remote-host
```

### Step 4: Start the Server

```bash
# Run with uv (hot reload is automatic for config changes)
uv run docker-mcp

# Alternative: run as module
uv run python -m docker_mcp.server

# Run with custom config
uv run docker-mcp --config config/custom-hosts.yml
```

The server will be available at `http://localhost:8000/mcp` (or configured port).

---

## 🐳 Docker Usage

### Container Management

```bash
# View logs
cd ~/.docker-mcp && docker-compose logs -f

# Stop services
cd ~/.docker-mcp && docker-compose down

# Restart services
cd ~/.docker-mcp && docker-compose restart

# Update to latest version
cd ~/.docker-mcp && docker-compose pull && docker-compose up -d

# Check service status
cd ~/.docker-mcp && docker-compose ps
```

### Building from Source

```bash
# Clone repository
git clone https://github.com/jmagar/docker-mcp
cd docker-mcp

# Build image locally
docker build -t docker-mcp:local .

# Run with local image
docker run -d \
  --name docker-mcp \
  -p 8000:8000 \
  -v ~/.docker-mcp/ssh:/home/dockermcp/.ssh:ro \
  -v ./config:/app/config:ro \
  -v docker-mcp-data:/app/data \
  docker-mcp:local
```

### Environment Variables

The containerized version supports all standard environment variables:

```yaml
# docker-compose.yaml environment section
environment:
  FASTMCP_HOST: "0.0.0.0"           # Bind address
  FASTMCP_PORT: "8000"              # Server port
  DOCKER_HOSTS_CONFIG: "/app/config/hosts.yml"  # Config path
  LOG_LEVEL: "INFO"                 # DEBUG, INFO, WARNING, ERROR
  LOG_DIR: "/app/data/logs"         # Log directory
  SSH_CONFIG_PATH: "/home/dockermcp/.ssh/config"  # SSH config
  RATE_LIMIT_PER_SECOND: "50.0"    # Rate limiting
  HOT_RELOAD: "true"                # Auto-reload config
```

### Volume Mounts

| Volume | Purpose | Mode |
|--------|---------|------|
| `~/.docker-mcp/ssh` | SSH keys and config | Read-only |
| `./config` | Host configurations | Read-only |
| `docker-mcp-data` | Logs and persistent data | Read-write |

### Security Notes

- Container runs as non-root user (`dockermcp:1000`)
- SSH keys are mounted read-only from dedicated directory
- Only required SSH keys are exposed to container (not entire ~/.ssh)
- Configuration files are mounted read-only
- No secrets are built into the image

---

## 📖 Real-World Usage Examples

### 🚀 Deploy a Full Stack Application Across Multiple Hosts

```javascript
// Deploy WordPress to production
await docker_compose({
  action: "deploy",
  host_id: "prod-web-1",
  stack_name: "wordpress",
  compose_content: `
    version: '3.8'
    services:
      wordpress:
        image: wordpress:latest
        ports:
          - "80:80"
        environment:
          WORDPRESS_DB_HOST: db
          WORDPRESS_DB_PASSWORD: secret
      db:
        image: mysql:5.7
        environment:
          MYSQL_ROOT_PASSWORD: secret
    volumes:
      wordpress_data:
      db_data:
  `
});
```

### 📊 Monitor All Containers Across Your Infrastructure

```javascript
// Get container status from all production hosts
const hosts = ["prod-web-1", "prod-web-2", "prod-db-1"];
for (const host of hosts) {
  const result = await docker_container({
    action: "list",
    host_id: host,
    all_containers: true
  });
  console.log(`${host}: ${result.containers.length} containers running`);
}
```

### 🔄 Rolling Updates with Zero Downtime

```javascript
// Update all web containers to latest version
const webHosts = ["web-1", "web-2", "web-3"];
for (const host of webHosts) {
  // Pull latest image
  await docker_container({
    action: "pull",
    host_id: host,
    container_id: "nginx:latest"
  });
  
  // Gracefully restart
  await docker_container({
    action: "restart",
    host_id: host,
    container_id: "nginx-web"
  });
  
  // Wait for health check
  await new Promise(r => setTimeout(r, 5000));
}
```

### 🚨 Emergency Response - Stop All Containers

```javascript
// Emergency: Stop all containers on a compromised host
await docker_container({
  action: "stop",
  host_id: "compromised-host",
  container_id: "$(docker ps -q)",  // Stops all running containers
  force: true
});
```

### 📝 Bulk Import from SSH Config

```python
# Import all Docker hosts from SSH config
await client.call_tool("docker_hosts", {
    "action": "add",
    "host_id": "production-1",
    "ssh_host": "192.168.1.10",
    "ssh_user": "dockeruser",
    "ssh_port": 22,
    "description": "Production server"
})

# List all configured hosts
result = await client.call_tool("docker_hosts", {
    "action": "list"
})

# Check port usage on a host
ports = await client.call_tool("docker_hosts", {
    "action": "ports",
    "host_id": "production-1"
})
```

### Container Management

```python
# List containers with pagination
result = await client.call_tool("docker_container", {
    "action": "list",
    "host_id": "production-1",
    "limit": 20,
    "all_containers": True
})

# Get detailed container information
info = await client.call_tool("docker_container", {
    "action": "info",
    "host_id": "production-1",
    "container_id": "my-app-container"
})

# Container lifecycle operations
await client.call_tool("docker_container", {
    "action": "start",
    "host_id": "production-1",
    "container_id": "my-app-container"
})

await client.call_tool("docker_container", {
    "action": "restart",
    "host_id": "production-1", 
    "container_id": "my-app-container"
})

# Pull Docker images
await client.call_tool("docker_container", {
    "action": "pull",
    "host_id": "production-1",
    "container_id": "nginx:latest"  # Note: container_id is image name for pull
})

# Get container logs
logs = await client.call_tool("docker_container", {
    "action": "logs",
    "host_id": "production-1",
    "container_id": "my-app-container",
    "lines": 100
})
```

### Docker Compose Stack Management

```python
# Deploy a new stack
compose_content = """
version: '3.8'
services:
  web:
    image: nginx:latest
    ports:
      - "80:80"
  db:
    image: postgres:13
    environment:
      POSTGRES_DB: myapp
"""

result = await client.call_tool("docker_compose", {
    "action": "deploy",
    "host_id": "production-1",
    "stack_name": "myapp",
    "compose_content": compose_content,
    "pull_images": True
})

# List all stacks
stacks = await client.call_tool("docker_compose", {
    "action": "list",
    "host_id": "production-1"
})

# Stack lifecycle management
await client.call_tool("docker_compose", {
    "action": "restart",
    "host_id": "production-1",
    "stack_name": "myapp"
})

# Pull all stack images
await client.call_tool("docker_compose", {
    "action": "pull",
    "host_id": "production-1", 
    "stack_name": "myapp"
})

# Get stack logs
logs = await client.call_tool("docker_compose", {
    "action": "logs",
    "host_id": "production-1",
    "stack_name": "myapp",
    "lines": 200
})

# Discover compose paths
paths = await client.call_tool("docker_compose", {
    "action": "discover",
    "host_id": "production-1"
})
```

---

## 🛠️ Available Tools

FastMCP Docker Manager provides 3 consolidated tools with action-based routing for streamlined operations:

### `docker_hosts`
Manage Docker host configurations and connectivity:
- `list` - List all configured Docker hosts
- `add` - Add a new Docker host for management
- `ports` - List port mappings for a host
- `compose_path` - Update host compose path configuration
- `import_ssh` - Import hosts from SSH configuration

### `docker_container`
Complete container lifecycle and image management:
- `list` - List containers with filtering and pagination
- `info` - Get detailed container information
- `start` / `stop` / `restart` - Container lifecycle operations
- `build` - Build container from Dockerfile
- `logs` - Stream or fetch container logs
- `pull` - **NEW** Pull Docker images to remote host

### `docker_compose`
Docker Compose stack deployment and management:
- `list` - List all stacks on a host
- `deploy` - Deploy new stacks with compose content
- `up` / `down` / `restart` - Stack lifecycle operations
- `build` - Build stack images
- `pull` - **NEW** Pull all images defined in compose stack
- `logs` - Stream or fetch stack logs
- `discover` - Auto-discover compose file locations

> 📝 **For detailed tool usage and examples**, see [TOOLS.md](TOOLS.md) which documents the consolidated tool design and migration from 13 individual tools to 3 action-based tools.

---

## ⚙️ Configuration

### 📁 Configuration Files

The server loads configuration in the following priority order:
1. Command line arguments (`--config path/to/config.yml`)
2. Environment variables (`.env` file)
3. Project configuration (`config/hosts.yml`)
4. User configuration (`~/.config/docker-mcp/hosts.yml`)
5. Docker context discovery (automatic fallback)

### 📝 YAML Configuration Schema

```yaml
# Complete configuration example
hosts:
  host-id:
    hostname: string          # SSH hostname or IP
    user: string             # SSH username
    port: int               # SSH port (default: 22)
    identity_file: string   # Path to SSH private key
    description: string     # Human-readable description
    tags: [string]          # Host tags for filtering
    compose_path: string    # Path for persistent compose files (auto-discovered if not set)
    enabled: bool          # Enable/disable host (default: true)

# Server configuration is via .env file only:
# FASTMCP_HOST: string    # Server bind address (default: 127.0.0.1)
# FASTMCP_PORT: int       # Server port (default: 8000)
# LOG_LEVEL: string       # Logging level (DEBUG, INFO, WARNING, ERROR)

# Note: Docker contexts are managed automatically by the core system.
# No additional docker_contexts configuration is needed in hosts.yml.
```

### 🔧 Environment Variables

- `DOCKER_HOSTS_CONFIG`: Path to YAML config file
- `FASTMCP_HOST`: Server bind address (default: 127.0.0.1, use 0.0.0.0 for external access)
- `FASTMCP_PORT`: Server port (default: 8000)
- `DOCKER_CONTEXT_AUTO_DISCOVER`: Auto-discover Docker contexts (default: true)
- `LOG_LEVEL`: Logging level (DEBUG, INFO, WARNING, ERROR)

### 🐳 Docker Deployment (Example)

To deploy with Docker, create a `Dockerfile` and `docker-compose.yml`:

```yaml
# docker-compose.yml example
version: '3.8'

services:
  fastmcp-docker-context:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ~/.docker:/root/.docker:ro  # Docker contexts and configs
      - ~/.ssh:/root/.ssh:ro         # SSH keys for Docker contexts
      - ./config:/app/config:ro
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - DOCKER_HOSTS_CONFIG=/app/config/hosts.yml
      - FASTMCP_HOST=0.0.0.0
      - FASTMCP_PORT=8000
      - LOG_LEVEL=INFO
    restart: unless-stopped
```

Note: You'll need to create your own Dockerfile based on your deployment requirements.

---

## 🔒 Security

### 🔐 Hybrid Security Model
**Container Operations:** Docker contexts provide secure Docker API connections over SSH tunnels
**Stack Operations:** Direct SSH connections for filesystem access and compose file management
- Docker contexts handle authentication and encrypted channels automatically
- SSH operations use configured identity files with proper key permissions
- All subprocess calls are marked with `# nosec B603` for legitimate Docker/SSH operations

### 🛡️ Code Security
- **Security Scanning**: Bandit integration with allowlisted legitimate operations
- **Command Validation**: Allowed Docker commands whitelist prevents injection attacks  
- **SSH Key Security**: Proper file permissions and key-based authentication only
- **No Credential Storage**: Never store passwords or credentials in configuration files

### 🌐 Network & Request Security
- **Rate Limiting Middleware**: Configurable request limits (default: 50 req/sec) with burst capacity
- **Error Handling Middleware**: Structured error responses without exposing sensitive information
- **Logging Security**: All operations logged with host_id context for audit trails
- **Host Isolation**: Each host operates independently with dedicated Docker contexts

### 📊 Security Monitoring
- **Dual Logging System**: Console + persistent file logging (`logs/mcp_server.log`)
- **Structured Logging**: JSON format with contextual information for security analysis
- **Performance Monitoring**: Request timing to detect potential DoS attempts
- **Error Tracking**: Comprehensive error logging for security incident investigation

---

## 💻 Development

### 📂 Project Structure

```
docker-mcp/
├── pyproject.toml           # Python project configuration
├── uv.lock                  # Lock file for dependencies
├── README.md               # This documentation
├── TOOLS.md                # Consolidated tools documentation
├── CLAUDE.md               # Project memory and development notes
├── .env.example            # Environment variables template
├── config/
│   ├── hosts.yml          # Default host configuration
│   ├── hosts.example.yml  # Example configuration
│   └── CLAUDE.md          # Configuration layer development notes
├── docker_mcp/           # Main package
│   ├── __init__.py
│   ├── server.py         # Main server entry point
│   ├── core/            # Core functionality
│   │   ├── __init__.py
│   │   ├── CLAUDE.md    # Core module development notes
│   │   ├── config_loader.py # Configuration loading and management
│   │   ├── docker_context.py # Docker context management
│   │   ├── compose_manager.py # Compose file management
│   │   ├── exceptions.py # Custom exceptions
│   │   ├── file_watcher.py # Configuration file watching
│   │   ├── logging_config.py # Logging configuration
│   │   └── ssh_config_parser.py # SSH config parsing
│   ├── models/          # Data models
│   │   ├── __init__.py
│   │   ├── CLAUDE.md    # Models layer development notes
│   │   ├── host.py      # Host models
│   │   └── container.py # Container models
│   ├── services/        # Business logic layer
│   │   ├── __init__.py
│   │   ├── CLAUDE.md    # Services layer development notes
│   │   ├── config.py    # Configuration service
│   │   ├── container.py # Container service
│   │   ├── host.py      # Host service
│   │   └── stack.py     # Stack service
│   ├── tools/           # MCP tools
│   │   ├── __init__.py
│   │   ├── CLAUDE.md    # Tools layer development notes
│   │   ├── containers.py # Container management tools
│   │   ├── stacks.py    # Stack deployment tools
│   │   └── logs.py      # Log tools
│   ├── middleware/      # Server middleware
│   │   ├── __init__.py
│   │   ├── CLAUDE.md    # Middleware development notes
│   │   ├── error_handling.py # Error handling middleware
│   │   ├── logging.py   # Logging middleware
│   │   ├── rate_limiting.py # Rate limiting middleware
│   │   └── timing.py    # Request timing middleware
│   └── prompts/        # AI prompts and templates
│       ├── __init__.py
│       ├── CLAUDE.md    # Prompts development notes
│       └── deployment.py # Deployment prompts
└── tests/              # Comprehensive test suite
    ├── conftest.py     # Shared fixtures and configuration
    ├── cleanup.py      # Test cleanup utilities
    ├── cleanup_utils.py # Cleanup helper functions
    ├── CLAUDE.md       # Test development notes
    ├── CLEANUP.md      # Test cleanup documentation
    ├── test_config.py  # Configuration tests
    ├── test_all_tools_pytest.py  # Comprehensive tool tests
    ├── test_core_tools_pytest.py # Core functionality tests
    ├── test_stack_operations_pytest.py # Stack operation tests
    ├── test_deployment_prompts.py # Deployment prompt tests
    ├── test_docker_context.py # Docker context tests
    ├── test_file_watcher.py # File watcher tests
    ├── test_host_models.py # Host model tests
    ├── test_integration_coverage_boost.py # Integration coverage tests
    ├── test_integration_mocked.py # Mocked integration tests
    ├── test_integration_simplified.py # Simplified integration tests
    ├── test_middleware.py # Middleware tests
    ├── test_models.py  # Data model tests
    ├── test_prompts.py # Prompt tests
    ├── test_services_comprehensive.py # Comprehensive service tests
    ├── test_ssh_config_parser.py # SSH config parser tests
    └── test_tools_comprehensive.py # Comprehensive tool tests
```

### 🚀 Development Setup

```bash
# Install with uv (recommended) - includes Python 3.10+ requirement
uv sync --dev

# Alternative: Install with pip (requires Python 3.10+)
pip install -e ".[dev]"

# Run server (hot reload is always enabled for config changes)
uv run docker-mcp

# Run with custom config
uv run docker-mcp --config config/dev-hosts.yml

# Validate configuration without starting server
uv run docker-mcp --validate-config

# Run tests with coverage (uses .cache/ directory)
uv run pytest                               # All tests
uv run pytest --cov=docker_mcp --cov-report=html  # With HTML coverage report
uv run pytest -k "not slow"                # Skip slow tests (port scanning, discovery)
uv run pytest -m integration               # Integration tests only
uv run pytest -m unit                      # Unit tests only

# Code formatting and linting (configured in pyproject.toml)
uv run ruff format .                        # Format code
uv run ruff check . --fix                  # Lint and auto-fix
uv run mypy docker_mcp/                     # Type checking

# Cache directories used
# .cache/ruff/          - Ruff linting cache
# .cache/mypy/          - MyPy type checking cache  
# .cache/pytest/        - Pytest cache
# .cache/coverage/      - Coverage data
# .cache/coverage_html/ - HTML coverage reports
```

### 🧪 Testing

The project uses pytest with FastMCP's in-memory testing pattern for fast, reliable tests:

```bash
# Run all tests
uv run pytest

# Run specific test categories
uv run pytest -k "not slow"           # Skip slow tests (port scanning, discovery)
uv run pytest -m integration          # Run integration tests only
uv run pytest -m unit                 # Run unit tests only

# Run specific test files
uv run pytest tests/test_config.py -v     # Configuration tests
uv run pytest tests/test_all_tools_pytest.py -v  # Consolidated tools tests
uv run pytest tests/test_core_tools_pytest.py -v  # Core functionality tests
uv run pytest tests/test_stack_operations_pytest.py -v  # Stack operation tests

# With coverage reporting
uv run pytest --cov=docker_mcp --cov-report=html --cov-report=term-missing

# Watch mode for development
uv run pytest-watch

# Run tests with timeout (configured: 60 seconds default)
uv run pytest --timeout=30            # Override timeout

# Run tests in parallel (pytest-xdist included in dev dependencies)
uv run pytest -n auto
```

#### 📋 Complete Test Structure
**Core Tests:**
- `test_config.py` - Configuration loading and validation tests
- `test_all_tools_pytest.py` - Comprehensive tests for all 3 consolidated MCP tools
- `test_core_tools_pytest.py` - Core functionality validation tests
- `test_stack_operations_pytest.py` - Detailed Docker Compose stack tests

**Component Tests:**
- `test_docker_context.py` - Docker context management tests
- `test_file_watcher.py` - Configuration hot reload tests
- `test_ssh_config_parser.py` - SSH config import tests
- `test_models.py` - Pydantic data model tests
- `test_host_models.py` - Host-specific model tests
- `test_middleware.py` - Middleware pipeline tests
- `test_services_comprehensive.py` - Service layer tests
- `test_tools_comprehensive.py` - Tool layer tests
- `test_prompts.py` - AI prompt tests
- `test_deployment_prompts.py` - Deployment-specific prompt tests

**Integration Tests:**
- `test_integration_coverage_boost.py` - Integration coverage tests
- `test_integration_mocked.py` - Mocked integration tests  
- `test_integration_simplified.py` - Simplified integration tests

**Test Infrastructure:**
- `conftest.py` - Shared pytest fixtures and configuration
- `cleanup.py` - Test cleanup utilities for orphaned resources
- `cleanup_utils.py` - Cleanup helper functions
- `CLAUDE.md` - Test development notes
- `CLEANUP.md` - Test cleanup documentation

#### 🏷️ Test Categories & Configuration
- **Unit tests** (`@pytest.mark.unit`): Fast tests for individual components
- **Integration tests** (`@pytest.mark.integration`): Tests requiring Docker host connectivity  
- **Slow tests** (`@pytest.mark.slow`): Tests taking >10 seconds (port scanning, discovery)
- **Timeout tests**: 60-second default timeout with thread-based cleanup
- **Coverage**: 85% minimum requirement, HTML reports in `.cache/coverage_html/`

---

## 📊 Logging

The server uses structured logging with configurable levels:
```python
import structlog

logger = structlog.get_logger()
logger.info("Container started", host_id="prod-1", container_id="abc123")
```

---

## 🔍 Troubleshooting

### ⚠️ Common Issues

**Docker Context Connection Failed**
```bash
# Test Docker context connectivity
docker --context remote-host ps

# List available Docker contexts
docker context ls

# Debug Docker context
docker --context remote-host --debug version
```

**Docker Permission Denied**
```bash
# Add user to docker group on remote host
sudo usermod -aG docker username
```

**Port Already in Use**
```bash
# Check what's using the port
sudo netstat -tulpn | grep :8000

# Kill the process or change port
export FASTMCP_PORT=8001
```

### 🐛 Debug Mode

```bash
# Enable debug logging
uv run docker-mcp --log-level DEBUG

# Debug Docker context operations
export DOCKER_DEBUG=1
uv run docker-mcp

# Debug specific host context
docker --context production-1 --debug ps
```

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

---

## 📄 License

MIT License

---

## 🗺️ Roadmap

- [ ] WebUI dashboard for visual management
- [ ] Support for Docker Swarm clusters
- [ ] Kubernetes integration
- [ ] Advanced monitoring and alerting
- [ ] Multi-architecture container support
- [ ] CI/CD pipeline integration