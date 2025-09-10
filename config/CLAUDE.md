# Configuration Layer - Development Memory

## Configuration Hierarchy and Loading

### Priority Order (Highest to Lowest)
```python
# Configuration loading priority in load_config()
1. Command line arguments (--config path)
2. Environment variables (.env file)
3. Project config (config/hosts.yml)
4. User config (~/.config/docker-mcp/hosts.yml)
5. Docker context discovery (automatic fallback)
6. Built-in defaults
```

### Configuration File Locations
```bash
# Project-specific configuration
./config/hosts.yml                    # Main project configuration
./config/hosts.example.yml            # Example/template configuration
./.env                                 # Environment variables (not committed)
./.env.example                        # Environment template (committed)

# User-specific configuration (future)
~/.config/docker-mcp/hosts.yml        # User global configuration
~/.ssh/config                         # SSH config for host discovery
```

## YAML Configuration Structure

### Complete hosts.yml Example
```yaml
# FastMCP Docker Context Manager Configuration

hosts:
  # Production host with full configuration
  production-1:
    hostname: 192.168.1.10
    user: dockeruser
    port: 22                           # Default SSH port
    identity_file: ~/.ssh/docker_host_key
    description: "Production Docker host"
    tags: ["production", "web", "critical"]
    compose_path: "/mnt/user/compose"  # Persistent compose file storage
    enabled: true                      # Default: true
    
  # Staging environment
  staging-1:
    hostname: 192.168.1.20
    user: dockeruser
    identity_file: ~/.ssh/docker_host_key
    description: "Staging environment"
    tags: ["staging", "test"]
    compose_path: "/home/docker/compose"
    
  # Local development (minimal config)
  development:
    hostname: localhost
    user: ${USER}                      # Environment variable expansion
    description: "Local development"
    tags: ["development", "local"]
    # compose_path auto-discovered if not specified
    # port defaults to 22
    # identity_file uses SSH defaults
    
  # Remote host with custom port
  remote-server:
    hostname: remote.example.com
    user: deployuser
    port: 2222                         # Custom SSH port
    identity_file: ~/.ssh/remote_key
    description: "Remote production server"
    tags: ["remote", "production"]
    compose_path: "/opt/docker/compose"

# Server configuration moved to .env file for security
# FASTMCP_HOST, FASTMCP_PORT, LOG_LEVEL
```

### Host Configuration Fields
```yaml
# Required fields
hostname: string                       # SSH hostname or IP address
user: string                          # SSH username

# Optional fields with defaults
port: integer                         # SSH port (default: 22)
identity_file: string                 # SSH key path (default: SSH agent/config)
description: string                   # Human-readable description
tags: list[string]                    # Categorization tags
compose_path: string                  # Custom compose file directory (auto-discovered if not set)
enabled: boolean                      # Whether host is active (default: true)
```

## Environment Variable Configuration

### .env File Structure
```bash
# FastMCP Docker Context Manager Configuration

# Server Configuration
FASTMCP_HOST=0.0.0.0                  # Bind address (default: 127.0.0.1)
FASTMCP_PORT=8000                     # Server port (default: 8000)

# Configuration File Path
DOCKER_HOSTS_CONFIG=config/hosts.yml  # Path to hosts config (default: config/hosts.yml)

# Logging Configuration
LOG_LEVEL=INFO                        # DEBUG, INFO, WARNING, ERROR (default: INFO)

# Optional: SSH Configuration
SSH_CONFIG_PATH=~/.ssh/config         # Custom SSH config path (default: ~/.ssh/config)
SSH_DEBUG=0                           # SSH debug level 0-3 (default: 0)

# Optional: Development Settings
DEVELOPMENT_MODE=false                # Enable development features (default: false)
HOT_RELOAD=true                       # Config file hot reload (default: true)
```

### Modern Environment Variable Patterns (Python 3.10+)
```python
import os
from typing import Annotated, Literal
from pydantic import BaseSettings, Field, validator
from pathlib import Path

# Pydantic v2 Settings with environment integration
class ModernDockerMCPSettings(BaseSettings):
    """Modern environment variable configuration with Pydantic v2."""
    
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore'
    )
    
    # FastMCP server settings
    fastmcp_host: Annotated[str, Field(
        default="127.0.0.1",
        description="FastMCP server bind address",
        json_schema_extra={"env": "FASTMCP_HOST"}
    )]
    
    fastmcp_port: Annotated[int, Field(
        default=8000,
        ge=1024, le=65535,
        description="FastMCP server port",
        json_schema_extra={"env": "FASTMCP_PORT"}
    )]
    
    # Logging configuration with validation
    log_level: Annotated[Literal["DEBUG", "INFO", "WARNING", "ERROR"], Field(
        default="INFO",
        description="Application log level",
        json_schema_extra={"env": "LOG_LEVEL"}
    )]
    
    # Docker configuration paths
    docker_hosts_config: Annotated[Path, Field(
        default=Path("config/hosts.yml"),
        description="Path to Docker hosts configuration",
        json_schema_extra={"env": "DOCKER_HOSTS_CONFIG"}
    )]
    
    # SSH configuration
    ssh_config_path: Annotated[Path | None, Field(
        default=None,
        description="Custom SSH config file path",
        json_schema_extra={"env": "SSH_CONFIG_PATH"}
    )]
    
    ssh_debug_level: Annotated[int, Field(
        default=0,
        ge=0, le=3,
        description="SSH debug verbosity (0-3)",
        json_schema_extra={"env": "SSH_DEBUG"}
    )]
    
    # Development settings
    development_mode: Annotated[bool, Field(
        default=False,
        description="Enable development features",
        json_schema_extra={"env": "DEVELOPMENT_MODE"}
    )]
    
    hot_reload: Annotated[bool, Field(
        default=True,
        description="Enable configuration hot reload",
        json_schema_extra={"env": "HOT_RELOAD"}
    )]
    
    # Performance settings
    max_concurrent_operations: Annotated[int, Field(
        default=10,
        ge=1, le=100,
        description="Maximum concurrent Docker operations",
        json_schema_extra={"env": "MAX_CONCURRENT_OPS"}
    )]
    
    operation_timeout: Annotated[float, Field(
        default=300.0,
        ge=10.0, le=3600.0,
        description="Default operation timeout in seconds",
        json_schema_extra={"env": "OPERATION_TIMEOUT"}
    )]
    
    # Security settings
    allowed_hosts: Annotated[list[str], Field(
        default_factory=lambda: ["localhost", "127.0.0.1"],
        description="Allowed client IP addresses/hostnames",
        json_schema_extra={"env": "ALLOWED_HOSTS"}
    )]
    
    # Field validators (Pydantic v2 syntax)
    @field_validator('docker_hosts_config', mode='after')
    @classmethod
    def validate_config_path(cls, v: Path) -> Path:
        """Ensure config path is accessible."""
        if not v.exists() and v.name != 'hosts.yml':
            # Only warn for non-default paths
            logger.warning(f"Configuration file not found: {v}")
        return v
    
    @field_validator('ssh_config_path', mode='after')
    @classmethod
    def validate_ssh_config(cls, v: Path | None) -> Path | None:
        """Validate SSH config file if specified."""
        if v and not v.exists():
            raise ValueError(f"SSH config file not found: {v}")
        return v
    
    # Model validator for cross-field validation
    @model_validator(mode='after')
    def validate_development_settings(self) -> 'ModernDockerMCPSettings':
        """Adjust settings for development mode."""
        if self.development_mode:
            # Override some settings for development
            if self.log_level == "INFO":
                self.log_level = "DEBUG"
            if not self.hot_reload:
                self.hot_reload = True
        return self

# Environment variable expansion with type safety
class SecureEnvExpansion:
    """Secure environment variable expansion with validation."""
    
    @staticmethod
    def expand_env_vars(value: str, allowed_vars: set[str] | None = None) -> str:
        """Securely expand environment variables with allowlist."""
        import re
        
        def replace_var(match):
            var_name = match.group(1)
            default_value = match.group(2) if match.group(2) else ""
            
            # Security: Only allow specific environment variables
            if allowed_vars and var_name not in allowed_vars:
                raise ValueError(f"Environment variable '{var_name}' not allowed")
            
            return os.getenv(var_name, default_value)
        
        # Match ${VAR} or ${VAR:default}
        pattern = r'\$\{([A-Z_][A-Z0-9_]*):?([^}]*)\}'
        return re.sub(pattern, replace_var, value)
    
    @staticmethod
    def get_safe_env_vars() -> set[str]:
        """Get list of safe environment variables for expansion."""
        return {
            'USER', 'HOME', 'PATH',
            'DOCKER_COMPOSE_PATH', 'SSH_AUTH_SOCK',
            'DEV_HOST', 'DEV_USER', 'STAGING_HOST', 'STAGING_USER'
        }

# Modern variable expansion in configuration
def expand_yaml_config(yaml_data: dict) -> dict:
    """Expand environment variables in YAML config with security."""
    import copy
    
    expanded = copy.deepcopy(yaml_data)
    safe_vars = SecureEnvExpansion.get_safe_env_vars()
    
    def expand_recursive(obj):
        if isinstance(obj, dict):
            return {k: expand_recursive(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [expand_recursive(item) for item in obj]
        elif isinstance(obj, str):
            return SecureEnvExpansion.expand_env_vars(obj, safe_vars)
        else:
            return obj
    
    return expand_recursive(expanded)

# Environment variable naming convention (updated)
ENV_VAR_PREFIXES = {
    'FASTMCP_*': 'FastMCP server settings',
    'DOCKER_*': 'Docker-specific configuration', 
    'SSH_*': 'SSH-related settings',
    'LOG_*': 'Logging configuration',
    'DEV_*': 'Development environment variables',
    'STAGING_*': 'Staging environment variables',
    'PROD_*': 'Production environment variables'
}
```

## Configuration Loading Implementation

### DockerMCPConfig Model
```python
from pydantic import BaseModel, Field

class ServerConfig(BaseModel):
    """FastMCP server configuration."""
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

class DockerHost(BaseModel):
    """Docker host configuration."""
    hostname: str
    user: str
    port: int = 22
    identity_file: str | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    compose_path: str | None = None
    enabled: bool = True

class DockerMCPConfig(BaseModel):
    """Complete Docker MCP configuration."""
    server: ServerConfig = Field(default_factory=ServerConfig)
    hosts: dict[str, DockerHost] = Field(default_factory=dict)
    
    # Configuration metadata
    config_source: str = "default"      # Tracks where config was loaded from
    auto_discovery: bool = True         # Enable Docker context auto-discovery
    hot_reload: bool = True             # Enable configuration file watching
```

### Configuration Loading Logic
```python
def load_config(config_path: str | None = None) -> DockerMCPConfig:
    """Load configuration with proper priority hierarchy."""
    
    # 1. Start with defaults
    config = DockerMCPConfig()
    
    # 2. Load .env file (if exists)
    load_dotenv()
    
    # 3. Determine config file path
    if config_path:
        # Command line argument (highest priority)
        hosts_config_path = config_path
    else:
        # Environment variable or default
        hosts_config_path = os.getenv("DOCKER_HOSTS_CONFIG", "config/hosts.yml")
    
    # 4. Load YAML configuration
    if Path(hosts_config_path).exists():
        with open(hosts_config_path) as f:
            yaml_data = yaml.safe_load(f)
            
        # Parse hosts configuration
        if "hosts" in yaml_data:
            for host_id, host_data in yaml_data["hosts"].items():
                # Environment variable expansion
                host_data = expand_yaml_config(host_data)
                config.hosts[host_id] = DockerHost(**host_data)
    
    # 5. Apply environment variable overrides
    if os.getenv("FASTMCP_HOST"):
        config.server.host = os.getenv("FASTMCP_HOST")
    if os.getenv("FASTMCP_PORT"):
        config.server.port = int(os.getenv("FASTMCP_PORT"))
    if os.getenv("LOG_LEVEL"):
        config.server.log_level = os.getenv("LOG_LEVEL")
    
    # 6. Auto-discovery fallback (if enabled and no hosts configured)
    if config.auto_discovery and not config.hosts:
        discovered_hosts = discover_docker_contexts()
        config.hosts.update(discovered_hosts)
        config.config_source = "auto-discovered"
    
    return config
```

## Configuration Patterns and Best Practices

### Host Configuration Patterns
```yaml
# Pattern 1: Minimal Configuration (local development)
localhost:
  hostname: localhost
  user: ${USER}
  description: "Local Docker host"
  tags: ["local", "development"]

# Pattern 2: Full Production Configuration  
production-web:
  hostname: 10.0.1.100
  user: dockeruser
  port: 22
  identity_file: ~/.ssh/production_key
  description: "Production web server cluster"
  tags: ["production", "web", "high-availability"]
  compose_path: "/mnt/persistent/compose"
  enabled: true

# Pattern 3: SSH Config Integration (imported hosts)
imported-host:
  hostname: server.example.com        # From SSH config Host entry
  user: deployuser                    # From SSH config User entry
  description: "Imported from SSH config"
  tags: ["imported", "ssh-config"]
  # port, identity_file inherited from SSH config
  
# Pattern 4: Development with Environment Variables
dev-dynamic:
  hostname: ${DEV_HOST:-localhost}    # Environment variable with default
  user: ${DEV_USER:-${USER}}         # Nested variable expansion  
  description: "Dynamic development host"
  tags: ["development", "dynamic"]
```

### Environment Configuration Patterns
```bash
# Development .env
FASTMCP_HOST=127.0.0.1               # Local only
FASTMCP_PORT=8000
LOG_LEVEL=DEBUG                      # Verbose logging
DOCKER_HOSTS_CONFIG=config/dev-hosts.yml
SSH_DEBUG=1                          # SSH debugging
DEVELOPMENT_MODE=true

# Production .env  
FASTMCP_HOST=0.0.0.0                 # Accept external connections
FASTMCP_PORT=8000
LOG_LEVEL=INFO                       # Standard logging
DOCKER_HOSTS_CONFIG=/etc/docker-mcp/hosts.yml
SSH_CONFIG_PATH=/etc/ssh/ssh_config  # System SSH config
HOT_RELOAD=false                     # Disable in production
```

### Tag-Based Host Organization
```yaml
hosts:
  # Infrastructure tags
  web-prod-1:
    tags: ["production", "web", "nginx"]
  web-prod-2: 
    tags: ["production", "web", "nginx"]
    
  # Environment tags
  staging-db:
    tags: ["staging", "database", "postgresql"]
  dev-local:
    tags: ["development", "local", "testing"]
    
  # Location tags
  datacenter-east:
    tags: ["production", "east", "primary"]
  datacenter-west:
    tags: ["production", "west", "backup"]
    
  # Role tags
  docker-host-1:
    tags: ["docker", "containers", "orchestration"]
  monitoring:
    tags: ["monitoring", "prometheus", "grafana"]
```

## SSH Integration Patterns

### SSH Config Host Discovery
```bash
# ~/.ssh/config entries automatically discovered
Host production-web-*
  HostName 10.0.1.%h
  User dockeruser
  IdentityFile ~/.ssh/production_key
  Port 22
  
Host staging-*
  HostName staging.example.com
  User deployuser
  IdentityFile ~/.ssh/staging_key
  Port 2222
  
Host dev-*
  HostName localhost
  User ${USER}
  Port 22
```

### Docker Context Integration
```python
# Docker contexts created automatically for each host
def ensure_docker_context(host_id: str, host_config: DockerHost) -> str:
    """Ensure Docker context exists for host."""
    context_name = f"docker-mcp-{host_id}"
    
    # Docker context handles SSH connection automatically
    docker_host_url = f"ssh://{host_config.user}@{host_config.hostname}:{host_config.port}"
    
    # Context creation (Docker handles SSH key management)
    subprocess.run([
        "docker", "context", "create", context_name,
        "--docker", f"host={docker_host_url}",
        "--description", f"Docker MCP context for {host_id}"
    ], capture_output=True)
    
    return context_name
```

## Configuration Validation and Error Handling

### Validation Patterns
```python
def validate_config(config: DockerMCPConfig) -> list[str]:
    """Validate configuration and return list of errors."""
    errors = []
    
    # Server validation
    if not (1 <= config.server.port <= 65535):
        errors.append(f"Invalid server port: {config.server.port}")
    
    if config.server.log_level not in ["DEBUG", "INFO", "WARNING", "ERROR"]:
        errors.append(f"Invalid log level: {config.server.log_level}")
    
    # Host validation
    for host_id, host in config.hosts.items():
        if not host.hostname:
            errors.append(f"Host {host_id}: hostname is required")
            
        if not host.user:
            errors.append(f"Host {host_id}: user is required")
            
        if not (1 <= host.port <= 65535):
            errors.append(f"Host {host_id}: invalid port {host.port}")
            
        # SSH key validation
        if host.identity_file:
            key_path = Path(host.identity_file).expanduser()
            if not key_path.exists():
                errors.append(f"Host {host_id}: SSH key not found: {host.identity_file}")
    
    return errors
```

### Error Handling Patterns
```python
def load_config_safe(config_path: str | None = None) -> DockerMCPConfig:
    """Load configuration with comprehensive error handling."""
    try:
        config = load_config(config_path)
        
        # Validate configuration
        errors = validate_config(config)
        if errors:
            error_msg = "\n".join([f"  - {error}" for error in errors])
            raise ValueError(f"Configuration validation failed:\n{error_msg}")
        
        return config
        
    except FileNotFoundError as e:
        raise ValueError(f"Configuration file not found: {e}")
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML syntax: {e}")
    except Exception as e:
        raise ValueError(f"Failed to load config: {e}")
```

## Hot Reload and Dynamic Configuration

### Configuration File Watching
```python
from watchfiles import awatch

async def watch_config_changes(config_path: str, reload_callback):
    """Watch configuration file for changes and reload."""
    async for changes in awatch(config_path):
        for change_type, file_path in changes:
            if file_path.endswith('.yml') or file_path.endswith('.yaml'):
                try:
                    new_config = load_config(config_path)
                    await reload_callback(new_config)
                    logger.info("Configuration reloaded", path=file_path)
                except Exception as e:
                    logger.error("Config reload failed", error=str(e), path=file_path)
```

### Dynamic Host Management
```python
# Runtime host addition (persisted to configuration)
async def add_host_to_config(host_id: str, host_config: DockerHost, 
                           config_path: str) -> None:
    """Add host to configuration and persist to file."""
    
    # Load current config
    config = load_config(config_path)
    
    # Add new host
    config.hosts[host_id] = host_config
    
    # Persist to YAML file
    hosts_data = {"hosts": {}}
    for hid, host in config.hosts.items():
        hosts_data["hosts"][hid] = host.model_dump(exclude_none=True)
    
    with open(config_path, 'w') as f:
        yaml.dump(hosts_data, f, default_flow_style=False, sort_keys=False)
    
    logger.info("Host added to configuration", host_id=host_id, config_path=config_path)
```

## Configuration Security and Best Practices

### Security Considerations
```yaml
# GOOD: Use SSH keys, not passwords
production-host:
  hostname: 10.0.1.100
  user: dockeruser
  identity_file: ~/.ssh/production_key  # SSH key authentication
  
# GOOD: Use environment variables for sensitive data
staging-host:
  hostname: ${STAGING_HOST}             # Not hardcoded
  user: ${STAGING_USER}
  
# AVOID: Never put credentials in config files
bad-host:
  hostname: server.com
  user: admin
  password: "secret123"                 # DON'T DO THIS!
```

### Configuration File Permissions
```bash
# Secure configuration file permissions
chmod 600 config/hosts.yml              # Read/write for owner only
chmod 600 .env                          # Environment file security

# SSH key security  
chmod 600 ~/.ssh/docker_host_key        # Private key permissions
chmod 644 ~/.ssh/docker_host_key.pub    # Public key permissions
```

### Configuration Organization
```bash
# Development workflow
config/
├── hosts.yml                          # Main configuration
├── hosts.example.yml                  # Template (committed)
├── hosts.dev.yml                      # Development-specific (optional)
├── hosts.staging.yml                  # Staging-specific (optional)
└── hosts.prod.yml                     # Production-specific (optional)

# Environment-specific loading
DOCKER_HOSTS_CONFIG=config/hosts.dev.yml    # Development
DOCKER_HOSTS_CONFIG=config/hosts.prod.yml   # Production
```

The configuration system provides a flexible, hierarchical approach to managing Docker hosts with proper security, validation, and dynamic capabilities that support both development and production workflows.
