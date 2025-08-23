# Docker Manager MCP

[![Docker Image](https://img.shields.io/badge/docker-ghcr.io%2Fjmagar%2Fdocker--mcp-blue)](https://github.com/jmagar/docker-mcp/pkgs/container/docker-mcp)
[![Build Status](https://github.com/jmagar/docker-mcp/actions/workflows/docker-build.yml/badge.svg)](https://github.com/jmagar/docker-mcp/actions/workflows/docker-build.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![FastMCP](https://img.shields.io/badge/FastMCP-2.11.3%2B-green)](https://github.com/jamesturk/fastmcp)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Control all your Docker hosts from one place.** Docker Manager MCP lets AI assistants manage containers, deploy stacks, and monitor services across your entire infrastructure - with zero configuration needed.

## 🚀 One-Line Install

```bash
curl -sSL https://raw.githubusercontent.com/jmagar/docker-mcp/main/install.sh | bash
```

That's it! The installer:
- ✅ Sets up SSH keys automatically
- ✅ Imports all your existing hosts from SSH config
- ✅ Configures secure authentication 
- ✅ Starts the service on port 8000

**No manual configuration needed.** If you can SSH to it, Docker Manager can control it.

## 🎯 What You Can Actually Do

- **Deploy applications** across multiple Docker hosts
- **Control containers** with start/stop/restart operations
- **Monitor services** with real-time log streaming
- **Manage stacks** using Docker Compose configurations
- **Update services** without downtime using rolling updates
- **Check port usage** to avoid conflicts
- **Auto-discover hosts** from your SSH configuration

## 🛠 The 3 Tools

### Tool 1: `docker_hosts`
Manage your Docker hosts and connectivity.

**Actions:**
- `list` - List all configured Docker hosts
- `add` - Add a new Docker host
- `ports` - List port mappings for a host
- `compose_path` - Update host compose path
- `import_ssh` - Import hosts from SSH config

**Schema for `add` action:**
```json
{
  "action": "add",
  "host_id": "production-1",        // Required: unique identifier
  "ssh_host": "192.168.1.100",      // Required: hostname or IP
  "ssh_user": "dockeruser",          // Required: SSH username
  "ssh_port": 22,                    // Optional: default 22
  "ssh_key_path": "~/.ssh/id_ed25519", // Optional: SSH key path
  "description": "Production server",   // Optional: description
  "tags": ["production", "web"],       // Optional: tags for filtering
  "compose_path": "/opt/compose"       // Optional: compose file path
}
```

### Tool 2: `docker_container`
Control containers across all your hosts.

**Actions:**
- `list` - List containers on a host
- `info` - Get detailed container information
- `start` / `stop` / `restart` - Container lifecycle
- `logs` - View or stream container logs
- `pull` - Pull Docker images
- `build` - Build containers from Dockerfile

**Schema for `list` action:**
```json
{
  "action": "list",
  "host_id": "production-1",    // Required: which host to query
  "all_containers": false,       // Optional: include stopped containers
  "limit": 20,                   // Optional: pagination limit
  "offset": 0                    // Optional: pagination offset
}
```

**Schema for lifecycle actions:**
```json
{
  "action": "start",             // Or "stop", "restart"
  "host_id": "production-1",     // Required: target host
  "container_id": "nginx-web",   // Required: container name or ID
  "force": false,                // Optional: force stop
  "timeout": 10                  // Optional: timeout in seconds
}
```

### Tool 3: `docker_compose`
Deploy and manage Docker Compose stacks.

**Actions:**
- `deploy` - Deploy a new stack
- `list` - List all stacks on a host
- `up` / `down` / `restart` - Stack lifecycle
- `logs` - View stack logs
- `build` / `pull` - Build or update images
- `discover` - Find compose files on host

**Schema for `deploy` action:**
```json
{
  "action": "deploy",
  "host_id": "production-1",        // Required: target host
  "stack_name": "wordpress",        // Required: stack identifier
  "compose_content": "...",         // Required: docker-compose.yml content
  "environment": {                  // Optional: environment variables
    "DB_PASSWORD": "secret"
  },
  "pull_images": true,              // Optional: pull latest images
  "recreate": false                 // Optional: force recreate containers
}
```

**Schema for `logs` action:**
```json
{
  "action": "logs",
  "host_id": "production-1",        // Required: target host
  "stack_name": "wordpress",        // Required: stack name
  "lines": 100,                     // Optional: number of lines
  "follow": false                   // Optional: stream logs
}
```

## 💡 Real-World Use Cases

### Deploy a WordPress Site
```yaml
# compose_content for WordPress deployment
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
      - db_data:/var/lib/mysql
volumes:
  db_data:
```

Use with: `docker_compose` action: `deploy`

### Monitor Multiple Hosts
1. Use `docker_hosts` with action `list` to get all hosts
2. For each host, use `docker_container` with action `list`
3. Use `docker_container` with action `logs` to monitor specific containers

### Emergency Container Management
- Stop all containers: `docker_container` with action `stop` and `force: true`
- Check what's using ports: `docker_hosts` with action `ports`
- Restart services: `docker_container` with action `restart`

## 🔧 Configuration (Optional!)

The beauty of Docker Manager MCP is that **you don't need to configure anything**. It automatically:
- Discovers your Docker hosts from SSH config
- Sets up secure connections
- Manages authentication

But if you want to customize:

### Add Custom Hosts
Create `~/.docker-mcp/config/hosts.yml`:
```yaml
hosts:
  my-server:
    hostname: 192.168.1.100
    user: myuser
    description: "My Docker server"
```

### Use Environment Variables
```bash
FASTMCP_PORT=8080  # Change port
LOG_LEVEL=DEBUG    # More verbose logging
```

## 🐳 Docker Deployment

Already included! The installer creates everything:

```bash
# Check status
cd ~/.docker-mcp && docker-compose ps

# View logs
cd ~/.docker-mcp && docker-compose logs

# Update to latest
cd ~/.docker-mcp && docker-compose pull && docker-compose up -d

# Stop service
cd ~/.docker-mcp && docker-compose down
```

## 🔒 Security Built-In

- **SSH key authentication only** (no passwords)
- **Dedicated SSH keys** for Docker Manager (isolated from your personal keys)
- **Read-only mounts** for configuration
- **Rate limiting** to prevent abuse
- **Non-root container** execution
- **Automatic security updates** via GitHub Actions

## 💻 For Developers

### Quick Dev Setup
```bash
git clone https://github.com/jmagar/docker-mcp
cd docker-mcp
uv sync
uv run docker-mcp
```

### Run Tests
```bash
uv run pytest                  # All tests
uv run pytest -k "not slow"    # Fast tests only
```

### Format Code
```bash
uv run ruff format .
uv run ruff check . --fix
```

## 📁 What's Inside

```
docker-mcp/
├── docker_mcp/         # Main application
│   ├── server.py       # FastMCP server with 3 consolidated tools
│   ├── core/           # Docker & SSH management
│   ├── services/       # Business logic
│   └── tools/          # Tool implementations
├── config/             # Example configurations
├── tests/              # Comprehensive test suite
└── install.sh          # One-line installer
```

## 🆘 Need Help?

### Container won't start?
```bash
# Check what's running on a port
docker_hosts action=ports host_id=my-server

# See container logs
docker_container action=logs host_id=my-server container_id=my-app
```

### Can't connect to a host?
```bash
# Test SSH connection
ssh user@your-host

# Import from SSH config
docker_hosts action=import_ssh
```

### Something else?
- Check logs: `~/.docker-mcp/data/logs/`
- Debug mode: `LOG_LEVEL=DEBUG`
- [Open an issue](https://github.com/jmagar/docker-mcp/issues)

## 🎉 Why Docker Manager MCP?

- **Zero Configuration** - Works out of the box with your existing setup
- **Universal Control** - Manage all your Docker hosts from one place
- **AI-Friendly** - Built for LLMs to orchestrate your infrastructure
- **Production Ready** - Rate limiting, error handling, and logging built-in
- **Secure by Default** - SSH keys only, no passwords ever
- **Always Up-to-Date** - Automatic updates via Docker

## 📄 License

MIT - Use it however you want!

## 🚀 Get Started Now

```bash
# Install in 10 seconds
curl -sSL https://raw.githubusercontent.com/jmagar/docker-mcp/main/install.sh | bash

# That's it! Start managing your Docker infrastructure
```

**Questions?** [Open an issue](https://github.com/jmagar/docker-mcp/issues) - we're here to help!