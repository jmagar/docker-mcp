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

## Modern Docker Context Management (Python 3.11+)

### Async Context Management with Resource Cleanup
```python
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncGenerator, Dict
import weakref

class ModernDockerContextManager:
    """Modern async context manager with proper resource management."""
    
    def __init__(self, config: DockerMCPConfig):
        self.config = config
        self._context_cache: Dict[str, str] = {}  # host_id -> context_name
        self._connection_pools: Dict[str, AsyncConnectionPool] = {}
        self._cleanup_tasks: weakref.WeakSet = weakref.WeakSet()
    
    @asynccontextmanager
    async def docker_operation_context(
        self, 
        host_id: str, 
        timeout: float = 30.0
    ) -> AsyncGenerator[DockerOperationContext, None]:
        """Modern context manager for Docker operations with timeout and cleanup."""
        
        async with AsyncExitStack() as stack:
            # Ensure context exists with timeout
            async with asyncio.timeout(timeout):
                context_name = await self.ensure_context(host_id)
            
            # Create operation context
            op_context = DockerOperationContext(
                host_id=host_id,
                context_name=context_name,
                start_time=time.perf_counter()
            )
            
            # Register cleanup
            cleanup_task = asyncio.create_task(self._cleanup_on_exit(op_context))
            self._cleanup_tasks.add(cleanup_task)
            stack.callback(cleanup_task.cancel)
            
            try:
                yield op_context
            finally:
                # Ensure proper cleanup even on cancellation
                if not cleanup_task.done():
                    cleanup_task.cancel()
    
    async def ensure_context(self, host_id: str) -> str:
        """Ensure Docker context exists with modern async patterns."""
        
        # 1. Check cache with async lock for thread safety
        async with asyncio.Lock():
            if host_id in self._context_cache:
                context_name = self._context_cache[host_id]
                if await self._context_exists(context_name):
                    return context_name
                else:
                    # Clear stale cache entry
                    del self._context_cache[host_id]
        
        # 2. Create context with retry logic
        context_name = f"docker-mcp-{host_id}"
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                await self._create_docker_context(host_id, context_name)
                
                # Cache successful creation
                async with asyncio.Lock():
                    self._context_cache[host_id] = context_name
                
                return context_name
                
            except* (DockerContextError, SSHConnectionError) as eg:
                if attempt == max_retries - 1:
                    # Log all attempts failed
                    errors = [str(e) for e in eg.exceptions]
                    logger.error(
                        "Failed to create Docker context after retries",
                        host_id=host_id,
                        attempts=max_retries,
                        errors=errors
                    )
                    raise
                
                # Wait before retry with exponential backoff
                wait_time = 2 ** attempt
                await asyncio.sleep(wait_time)
    
    async def batch_ensure_contexts(
        self, 
        host_ids: list[str]
    ) -> dict[str, str | Exception]:
        """Create multiple contexts concurrently with proper error handling."""
        
        async def ensure_single_context(host_id: str):
            try:
                context_name = await self.ensure_context(host_id)
                return host_id, context_name
            except Exception as e:
                return host_id, e
        
        # Use TaskGroup for concurrent context creation (Python 3.11+)
        results = {}
        
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(ensure_single_context(host_id))
                for host_id in host_ids
            ]
        
        # Process results
        for task in tasks:
            host_id, result = await task
            results[host_id] = result
        
        return results

class DockerOperationContext:
    """Context object for Docker operations with automatic resource tracking."""
    
    def __init__(self, host_id: str, context_name: str, start_time: float):
        self.host_id = host_id
        self.context_name = context_name
        self.start_time = start_time
        self.resources: list[Any] = []
        self.logger = structlog.get_logger()
    
    def add_resource(self, resource: Any):
        """Track a resource for automatic cleanup."""
        self.resources.append(resource)
    
    async def cleanup(self):
        """Clean up all tracked resources."""
        for resource in reversed(self.resources):
            try:
                if hasattr(resource, 'aclose'):
                    await resource.aclose()
                elif hasattr(resource, 'close'):
                    if asyncio.iscoroutinefunction(resource.close):
                        await resource.close()
                    else:
                        resource.close()
            except Exception as e:
                await self.logger.awarning(
                    "Error during resource cleanup",
                    resource_type=type(resource).__name__,
                    error=str(e)
                )
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
