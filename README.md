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

**Simply talk to your AI assistant in plain English.** No complex commands or JSON needed - just describe what you want to do with your Docker infrastructure.

### Tool 1: `docker_hosts`
Simplified Docker hosts management tool.

**Actions:**
• **list**: List all configured Docker hosts
  - Required: none

• **add**: Add a new Docker host (auto-runs test_connection and discover)
  - Required: host_id, ssh_host, ssh_user
  - Optional: ssh_port (default: 22), ssh_key_path, description, tags, enabled (default: true)

• **ports**: List or check port usage on a host
  - Required: host_id
  - Optional: port (for availability check)

• **import_ssh**: Import hosts from SSH config (auto-runs test_connection and discover for each)
  - Required: none
  - Optional: ssh_config_path, selected_hosts

• **cleanup**: Docker system cleanup operations
  - Required: host_id, cleanup_type
  - Valid cleanup_type: "check" | "safe" | "moderate" | "aggressive"

• **test_connection**: Test host connectivity (also runs discover)
  - Required: host_id

• **discover**: Discover paths and capabilities on hosts
  - Required: host_id (use 'all' to discover all hosts sequentially)
  - Discovers: compose_path, appdata_path
  - Single host: Fast discovery (5-15 seconds)
  - All hosts: Sequential discovery (30-60 seconds total)

• **edit**: Modify host configuration
  - Required: host_id
  - Optional: ssh_host, ssh_user, ssh_port, ssh_key_path, description, tags, compose_path, appdata_path, enabled

• **remove**: Remove host from configuration
  - Required: host_id

• **disk_usage**: Read-only Docker disk usage summary (alias of cleanup check)
  - Required: host_id

**Natural language examples:**
```
"Add a new Docker host called production-1 at 192.168.1.100 with user dockeruser"
"Add host staging at 10.0.1.50 using SSH key ~/.ssh/staging_key"
"List all my Docker hosts"
"Check what ports are being used on production-1"
"Import hosts from my SSH config"
"Clean up Docker on production-1 using safe mode"
"Test connection to staging-server"
"Discover capabilities on all hosts"
"Update the compose path for production-1 to /opt/stacks"
"Remove the old-server host from my configuration"
```

### Tool 2: `docker_container`
Consolidated Docker container management tool.

**Actions:**
• **list**: List containers on a host
  - Required: host_id
  - Optional: all_containers, limit, offset

• **info**: Get container information
  - Required: host_id, container_id

• **start**: Start a container
  - Required: host_id, container_id
  - Optional: force, timeout

• **stop**: Stop a container
  - Required: host_id, container_id
  - Optional: force, timeout

• **restart**: Restart a container
  - Required: host_id, container_id
  - Optional: force, timeout

• **build**: Build/rebuild a container
  - Required: host_id, container_id
  - Optional: force, timeout

• **logs**: Get container logs
  - Required: host_id, container_id
  - Optional: follow, lines

• **stats**: One-shot resource usage snapshot
  - Required: host_id, container_id

• **ports**: Show exposed host bindings for a container
  - Required: host_id, container_id

**Natural language examples:**
```
"List all containers on production-1"
"Show all containers including stopped ones on staging"
"Get info about the nginx container on production-1"
"Start the wordpress container on production-1"
"Stop the mysql database on staging"
"Restart the web server container on production-1"
"Force stop the stuck container on production-1 with 30 second timeout"
"Build the app container on staging"
"Show logs for nginx on production-1"
"Stream live logs from the api container on production-1"
"Show last 500 lines from the database logs on staging"
```

### Tool 3: `docker_compose`
Consolidated Docker Compose stack management tool.

**Actions:**
• **list**: List stacks on a host
  - Required: host_id

• **deploy**: Deploy a stack
  - Required: host_id, stack_name, compose_content
  - Optional: environment, pull_images, recreate

• **up/down/restart/build**: Manage stack lifecycle
  - Required: host_id, stack_name
  - Optional: options

• **discover**: Discover compose paths on a host
  - Required: host_id

• **logs**: Get stack logs
  - Required: host_id, stack_name
  - Optional: follow, lines, services (subset)

• **migrate**: Migrate stack between hosts
  - Required: host_id, target_host_id, stack_name
  - Optional: remove_source, skip_stop_source, start_target, dry_run

• **ps**: Show stack services (status and ports)
  - Required: host_id, stack_name

**Natural language examples:**
```
"List all stacks on production-1"
"Deploy wordpress stack to production-1 with this compose file: <content>"
"Deploy plex to media-server with DB_PASSWORD=secret123"
"Bring up the wordpress stack on production-1"
"Take down the old-app stack on staging"
"Restart the plex stack on media-server"
"Build the development stack on staging"
"Discover compose files on production-1"
"Show logs from the wordpress stack on production-1"
"Stream live logs from plex stack on media-server"
"Show last 200 lines from api-stack logs on staging"
"Migrate wordpress from old-server to new-server"
"Do a dry run migration of plex from server1 to server2"
"Migrate database stack and remove it from the source after"
```

## 🏗 Architecture: Why 3 Consolidated Tools?

Docker Manager MCP uses the **Consolidated Action-Parameter Pattern** instead of 27 individual tools. This architectural choice provides:

### **Token Efficiency** 
- **2.6x more efficient**: Our 3 tools use ~5k tokens vs. 27 individual tools using ~9.7k tokens
- **Better scaling**: Adding new actions to existing tools is more efficient than creating new tools
- **Context savings**: Each tool adds ~400-500 tokens - consolidation reduces this multiplicatively

### **Complex Operation Support**
Docker management requires sophisticated multi-step operations:
- **Migration**: stop → verify → archive → transfer → deploy → validate
- **Cleanup**: analyze → confirm → execute → verify
- **Deployment**: validate → pull → configure → start → health-check

### **Hybrid Connection Model** 
Different operations need different approaches:
- **Container operations**: Docker contexts (API over SSH tunnel) for efficiency
- **Stack operations**: Direct SSH (filesystem access) for compose file management

### **Service Layer Benefits**
- **Centralized validation**: Consistent input validation across all operations  
- **Error handling**: Uniform error reporting and recovery
- **Resource management**: Connection pooling, context caching, and cleanup
- **Business logic**: Complex orchestration that simple decorators can't handle

*For technical details, see [`docs/consolidated-action-pattern.md`](docs/consolidated-action-pattern.md)*

---

**🚀 Advanced Migration Features:**

**Transfer Method:**
- **Rsync**: Universal compatibility for all Docker environments with compression and delta transfers

**Enhanced Safety Features:**
- **Always stops containers by default** (must explicitly skip with `skip_stop_source: true` - NOT recommended)
- **Verifies all containers are completely stopped** before archiving (prevents data corruption)
- **Archive integrity verification** before transfer
- **Filesystem sync delays** after container shutdown
- **Reliable transfers** with rsync for data consistency

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

Use with: "Deploy wordpress stack to production-1 with this compose file"

### Monitor Multiple Hosts
Simply ask your AI assistant:
- "Show me all my Docker hosts"
- "List all containers on each host"
- "Show me the logs from nginx on production-1"
- "Stream live logs from all my database containers"

### Emergency Container Management
When things go wrong, just describe the problem:
- "Force stop all containers on production-1"
- "What's using port 80 on my staging server?"
- "Restart all my web services"
- "Show me what's currently running on production-1"

### Migrate Stack to New Host
Perfect for hardware upgrades, load balancing, or moving to faster storage:

1. **Test the migration** (dry run):
   ```
   "Do a dry run migration of wordpress from old-server to new-server"
   ```

2. **Rsync Migration** (universal compatibility):
   - **ALWAYS stops containers by default** (safety first!)
   - **Verifies all containers are completely stopped** (prevents data corruption)
   - Waits for filesystem sync to ensure data consistency
   - Archives all volumes and data (excludes cache, logs, node_modules)
   - **Verifies archive integrity** before transfer
   - Transfers via rsync with compression
   - Updates paths for the target host
   - Deploys and starts on the target
   - Preserves all your data and configurations

**The migration intelligently handles:**
- Named Docker volumes and bind mounts
- Compose configurations and environment variables
- Path translation between different host structures
- **Rsync transfer** with compression and delta synchronization
- **Data consistency** through container stopping and verification

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
  # Production Docker Host
  production-server:
    hostname: 192.168.1.100
    user: myuser
    description: "Production Docker server"
    compose_path: /opt/compose       # Where to store compose files
    appdata_path: /opt/appdata       # Container data directory
    
  # Staging Docker Host  
  staging-server:
    hostname: 192.168.1.101
    user: myuser
    description: "Staging Docker server" 
    compose_path: /opt/compose       # Where to store compose files
    appdata_path: /opt/appdata       # Container data directory
```


### Use Environment Variables
```bash
FASTMCP_PORT=8080  # Change port
LOG_LEVEL=DEBUG    # More verbose logging
```

### 🚀 Transfer Method

**Rsync Transfer Features:**
- **Universal Compatibility**: Works with any Linux Docker host
- **Data Integrity**: File-level verification with checksums
- **Incremental**: Delta sync with compression for efficiency
- **Metadata**: Preserves permissions, timestamps, and ownership
- **Reliable**: File-by-file transfer with retry capabilities
- **Use Case**: All Docker environments, universal compatibility

**Performance:**
- **Large datasets**: Efficient delta transfers reduce bandwidth
- **Small stacks**: Quick transfers with minimal overhead
- **Database migrations**: Container stopping ensures data consistency

## 🐳 Docker Deployment

Already included! The installer creates everything:

```bash
# Check status
cd ~/.docker-mcp && docker compose ps

# View logs
cd ~/.docker-mcp && docker compose logs

# Update to latest
cd ~/.docker-mcp && docker compose pull && docker compose up -d

# Stop service
cd ~/.docker-mcp && docker compose down
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
Just ask your AI assistant:
```
"What's running on port 80 on my-server?"
"Show me the logs from my-app container on my-server"
"Why won't my nginx container start on production-1?"
```

### Can't connect to a host?
Let your AI assistant help troubleshoot:
```
"Test the connection to my staging server"
"Import all hosts from my SSH config"
"Add my new server at 192.168.1.100 to Docker Manager"
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
