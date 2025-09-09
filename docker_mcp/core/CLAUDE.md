# Core Module - Development Memory

## Configuration Management Patterns

### Pydantic Settings with Environment Variables
```python
class ServerConfig(BaseModel):
    host: str = Field(default="127.0.0.1", alias="FASTMCP_HOST")
    port: int = Field(default=8000, alias="FASTMCP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

class DockerMCPConfig(BaseSettings):
    hosts: dict[str, DockerHost] = Field(default_factory=dict)
    server: ServerConfig = Field(default_factory=ServerConfig)
    model_config = {"env_file": ".env", "extra": "ignore"}
```

### Configuration Hierarchy
1. Command line arguments (`--config`)
2. Project config (`config/hosts.yml`)
3. User config (`~/.config/docker-mcp/hosts.yml`)
4. Environment variables (Field aliases)

## Docker Context Management

### Context Caching Pattern
```python
class DockerContextManager:
    def __init__(self, config: DockerMCPConfig):
        self._context_cache: dict[str, str] = {}  # host_id -> context_name
        
    async def ensure_context(self, host_id: str) -> str:
        # 1. Check cache first
        if host_id in self._context_cache:
            if await self._context_exists(context_name):
                return context_name
            else:
                del self._context_cache[host_id]  # Clear stale cache
        
        # 2. Create or find context
        # 3. Cache and return
```

### SSH URL Construction
```python
# Build SSH URL for Docker context
ssh_url = f"ssh://{host_config.user}@{host_config.hostname}"
if host_config.port != 22:
    ssh_url += f":{host_config.port}"
```

### Subprocess Security
```python
# Mark legitimate Docker/SSH calls with security comment
result = await asyncio.get_event_loop().run_in_executor(
    None,
    lambda: subprocess.run(  # nosec B603
        cmd, check=False, capture_output=True, text=True
    )
)
```

## Compose File Management

### Path Resolution Strategy
```python
async def get_compose_path(self, host_id: str) -> str:
    # 1. Use explicit compose_path if configured
    if host_config.compose_path:
        return host_config.compose_path
    
    # 2. Auto-discover common paths
    return await self._auto_discover_compose_path(host_id)
```

## Hot Reload Pattern

### File Watching with Callbacks
```python
class ConfigFileWatcher:
    def __init__(self, config_path: str, 
                 reload_callback: Callable[[DockerMCPConfig], Awaitable[None]]):
        self.reload_callback = reload_callback
        
    async def _watch_loop(self):
        async for changes in awatch(self.config_path):
            new_config = load_config(str(self.config_path))
            await self.reload_callback(new_config)
```

## SSH Config Import

### Validation Pattern
```python
class SSHConfigEntry:
    def is_valid(self) -> bool:
        # Skip wildcard entries
        if "*" in self.name or "?" in self.name:
            return False
        # Require hostname
        return bool(self.hostname)
    
    def to_docker_host(self) -> DockerHost:
        return DockerHost(
            hostname=self.hostname or self.name,
            user=self.user or "root",
            tags=["imported", "ssh-config"]
        )
```

## Exception Patterns

All core exceptions inherit from base:
- `DockerMCPError` - Base exception
- `DockerContextError` - Context operations
- `ConfigurationError` - Config loading/validation
