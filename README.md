# FastMCP Docker Context Manager

A powerful MCP (Model Context Protocol) server that enables remote Docker container and stack management across multiple hosts. Built with FastMCP's HTTP streaming transport, it provides real-time container monitoring, log streaming, and multi-host Docker orchestration through a modern, secure interface.

## 📚 Table of Contents

- [✨ Key Features](#-key-features)
- [🏗️ Architecture](#️-architecture)
- [🚀 Quick Start](#-quick-start)
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

## ✨ Key Features

- **Multi-Host Docker Management**: Connect to multiple remote Docker hosts via Docker contexts
- **Real-time Log Streaming**: Stream container logs in real-time using HTTP streaming
- **Stack Deployment**: Deploy Docker Compose stacks with persistent file management
- **Container Lifecycle Management**: Start, stop, and monitor containers across hosts
- **Port Management & Conflict Detection**: List all port mappings and automatically detect conflicts
- **Docker Context Integration**: Native Docker context support with automatic connection management
- **HTTP Streaming Transport**: Uses FastMCP's modern HTTP streaming for real-time updates
- **Production Ready**: Includes security best practices and monitoring capabilities

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

The server follows a layered architecture:
- **Tools Layer**: MCP tool definitions and parameter validation
- **Services Layer**: Business logic, orchestration, and ToolResult formatting
- **Core Layer**: Docker contexts, SSH connections, and configuration management
- **Middleware Layer**: Error handling, logging, rate limiting, and request timing

## 🚀 Quick Start

### Step 1: Installation

```bash
# Clone the repository
git clone <repository-url>
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

## 📖 Usage Examples

These examples show how to use the MCP tools programmatically via the MCP client:

### Adding Remote Docker Hosts

```python
# Via MCP tool call
await client.call_tool("add_docker_host", {
    "host_id": "production-1",
    "ssh_host": "192.168.1.10",
    "ssh_user": "dockeruser"
})
```

### Container Management

```python
# List all containers on a host
result = await client.call_tool("list_containers", {
    "host_id": "production-1"
})

# Get container information
info = await client.call_tool("get_container_info", {
    "host_id": "production-1",
    "container_id": "abc123"
})

# Manage container (start/stop/restart)
result = await client.call_tool("manage_container", {
    "host_id": "production-1",
    "container_id": "abc123",
    "action": "start"
})

# Get container logs
logs = await client.call_tool("get_container_logs", {
    "host_id": "production-1",
    "container_id": "abc123",
    "lines": 50
})
```

### Stack Deployment

```python
# Deploy a Docker Compose stack
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

result = await client.call_tool("deploy_stack", {
    "host_id": "production-1",
    "stack_name": "myapp",
    "compose_content": compose_content
})
```

### Container Logs

```python
# Get container logs via MCP tool
logs = await client.call_tool("get_container_logs", {
    "host_id": "production-1",
    "container_id": "abc123",
    "lines": 100
})

# Note: Real-time log streaming is handled internally by the MCP server
```

---

## 🛠️ Available Tools

| Tool | Description |
|------|-------------|
| `add_docker_host` | Add a remote Docker host for management |
| `list_docker_hosts` | List all configured Docker hosts |
| `list_containers` | List all containers with compose file information |
| `get_container_info` | Get detailed information about a specific container |
| `manage_container` | Unified container management (start, stop, restart) |
| `deploy_stack` | Deploy a Docker Compose stack with persistent files |
| `manage_stack` | Manage stack lifecycle (up, down, restart, etc.) |
| `list_stacks` | List all Docker Compose stacks on a host |
| `get_container_logs` | Get recent logs from a container |
| `list_host_ports` | List all port mappings and detect conflicts |
| `discover_compose_paths` | Auto-discover compose file locations |
| `update_host_config` | Update host compose path configuration |
| `import_ssh_config` | Import hosts from SSH configuration |

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

# Server configuration is via .env file:
# FASTMCP_HOST: string    # Server bind address (default: 127.0.0.1)
# FASTMCP_PORT: int       # Server port (default: 8000)
# LOG_LEVEL: string       # Logging level (DEBUG, INFO, WARNING, ERROR)

docker_contexts:
  auto_discover: bool    # Auto-discover Docker contexts (default: true)
  connect_timeout: int   # Docker context connection timeout (seconds)
  command_timeout: int   # Docker command timeout (seconds)
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

### 🔐 Docker Context Security
- All connections secured via Docker's native context system
- No direct SSH handling - Docker manages all authentication
- Use dedicated SSH keys for Docker context connections
- Implement key rotation policies through Docker contexts

### 🛡️ Docker Security
- Use least privilege Docker users on remote hosts
- All remote connections via Docker contexts only
- Enable Docker daemon TLS for additional security
- Regular security audits and logging

### 🌐 Network Security
- Isolate Docker management network
- Use VPN or private networks when possible
- Docker contexts provide encrypted communication channels
- Monitor and log all operations

---

## 💻 Development

### 📂 Project Structure

```
docker-mcp/
├── pyproject.toml           # Python project configuration
├── uv.lock                  # Lock file for dependencies
├── README.md               # This documentation
├── .env.example            # Environment variables template
├── config/
│   ├── hosts.yml          # Default host configuration
│   └── hosts.example.yml  # Example configuration
├── docker_mcp/           # Main package
│   ├── __init__.py
│   ├── server.py         # Main server entry point
│   ├── core/            # Core functionality
│   │   ├── __init__.py
│   │   ├── config.py    # Configuration management
│   │   ├── docker_context.py # Docker context management
│   │   ├── compose_manager.py # Compose file management
│   │   ├── exceptions.py # Custom exceptions
│   │   ├── file_watcher.py # Configuration file watching
│   │   ├── logging_config.py # Logging configuration
│   │   └── ssh_config_parser.py # SSH config parsing
│   ├── models/          # Data models
│   │   ├── __init__.py
│   │   ├── host.py      # Host models
│   │   └── container.py # Container models
│   ├── services/        # Business logic layer
│   │   ├── __init__.py
│   │   ├── config.py    # Configuration service
│   │   ├── container.py # Container service
│   │   ├── host.py      # Host service
│   │   └── stack.py     # Stack service
│   ├── tools/           # MCP tools
│   │   ├── __init__.py
│   │   ├── containers.py # Container management tools
│   │   ├── stacks.py    # Stack deployment tools
│   │   └── logs.py      # Log tools
│   ├── middleware/      # Server middleware
│   │   ├── __init__.py
│   │   ├── error_handling.py # Error handling middleware
│   │   ├── logging.py   # Logging middleware
│   │   ├── rate_limiting.py # Rate limiting middleware
│   │   └── timing.py    # Request timing middleware
│   └── prompts/        # AI prompts and templates
│       ├── __init__.py
│       └── deployment.py # Deployment prompts
└── tests/              # Comprehensive test suite
    ├── conftest.py     # Shared fixtures and configuration
    ├── cleanup.py      # Test cleanup utilities
    ├── cleanup_utils.py # Cleanup helper functions
    ├── test_config.py  # Configuration tests
    ├── test_all_tools_pytest.py  # Comprehensive tool tests
    ├── test_core_tools_pytest.py # Core functionality tests
    ├── test_stack_operations_pytest.py # Stack operation tests
    └── ...             # Additional test files for complete coverage
```

### 🚀 Development Setup

```bash
# Install with uv (recommended)
uv sync --dev

# Alternative: Install with pip
pip install -e ".[dev]"

# Run server (config hot reload is automatic)
uv run docker-mcp

# Run with custom config
uv run docker-mcp --config config/dev-hosts.yml

# Run tests
uv run pytest

# Code formatting and linting
uv run ruff format .
uv run ruff check . --fix
uv run mypy docker_mcp/
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
uv run pytest tests/test_core_tools_pytest.py -v  # Core functionality tests

# With coverage reporting
uv run pytest --cov=docker_mcp --cov-report=html --cov-report=term-missing

# Watch mode for development
uv run pytest-watch

# Run tests in parallel (if you have pytest-xdist installed)
uv run pytest -n auto
```

#### 📋 Test Structure
- `tests/test_config.py` - Configuration loading and validation tests
- `tests/test_all_tools_pytest.py` - Comprehensive tests for all MCP tools
- `tests/test_core_tools_pytest.py` - Core functionality validation tests
- `tests/test_stack_operations_pytest.py` - Detailed Docker Compose stack tests
- `tests/conftest.py` - Shared pytest fixtures and configuration
- Additional test files for services, middleware, models, and integration testing

#### 🏷️ Test Categories
- **Unit tests**: Fast tests for individual components
- **Integration tests**: Tests that interact with real Docker hosts (marked with `@pytest.mark.integration`)
- **Slow tests**: Tests that take >10 seconds (port scanning, compose discovery, marked with `@pytest.mark.slow`)

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