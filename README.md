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

### Deploy Applications Instantly
```javascript
// Deploy WordPress with one command
await docker_compose({
  action: "deploy",
  host_id: "my-server",
  stack_name: "wordpress",
  compose_content: `your-compose-file-here`
});
```

### Control Containers Everywhere
```javascript
// Check what's running on all your servers
const hosts = ["web-1", "web-2", "db-1", "cache-1"];
for (const host of hosts) {
  const containers = await docker_container({
    action: "list",
    host_id: host
  });
  console.log(`${host}: ${containers.length} containers`);
}
```

### Monitor Everything in Real-Time
```javascript
// Stream logs from any container on any host
await docker_container({
  action: "logs",
  host_id: "production",
  container_id: "my-app",
  follow: true
});
```

### Update Services Without Downtime
```javascript
// Rolling update across multiple hosts
for (const host of ["web-1", "web-2", "web-3"]) {
  await docker_container({
    action: "pull",
    host_id: host,
    container_id: "nginx:latest"
  });
  
  await docker_container({
    action: "restart", 
    host_id: host,
    container_id: "nginx"
  });
}
```

## 💡 Real-World Examples

### Deploy a Multi-Container App
Deploy a complete application stack with database, cache, and web server:

```javascript
const composeFile = `
version: '3.8'
services:
  web:
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
  
  cache:
    image: redis:alpine
    
volumes:
  db_data:
`;

await docker_compose({
  action: "deploy",
  host_id: "production",
  stack_name: "my-blog",
  compose_content: composeFile
});
```

### Emergency Response
Something wrong? Stop everything on a host instantly:

```javascript
// Stop ALL containers on compromised host
const containers = await docker_container({
  action: "list",
  host_id: "compromised-host"
});

for (const container of containers) {
  await docker_container({
    action: "stop",
    host_id: "compromised-host",
    container_id: container.id,
    force: true
  });
}
```

### Automated Deployments
Deploy to staging, test, then deploy to production:

```javascript
// Deploy to staging
await docker_compose({
  action: "deploy",
  host_id: "staging",
  stack_name: "my-app",
  compose_content: appConfig
});

// Run tests...
// If tests pass, deploy to production

await docker_compose({
  action: "deploy", 
  host_id: "production",
  stack_name: "my-app",
  compose_content: appConfig
});
```

### Monitor Resource Usage
Check which containers are using the most resources:

```javascript
const info = await docker_container({
  action: "info",
  host_id: "production",
  container_id: "database"
});

console.log(`Memory: ${info.memory_usage}`);
console.log(`CPU: ${info.cpu_percentage}%`);
```

## 🛠 Just 3 Simple Tools

We've simplified everything down to just 3 tools that do everything you need:

### `docker_hosts`
Manage your Docker hosts:
- **list** - See all your connected hosts
- **add** - Connect to a new Docker host
- **ports** - Check what ports are in use
- **import_ssh** - Auto-import from SSH config

### `docker_container`  
Control containers:
- **list** - See what's running
- **info** - Get container details
- **start/stop/restart** - Control containers
- **logs** - View or stream logs
- **pull** - Update images

### `docker_compose`
Deploy and manage stacks:
- **deploy** - Deploy new applications
- **list** - See deployed stacks
- **up/down/restart** - Control entire stacks
- **logs** - View stack logs
- **build/pull** - Build or update images

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