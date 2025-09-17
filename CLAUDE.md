# FastMCP Docker Context Manager - Project Memory

## Core Architecture

### Hybrid Connection Model
- **Container Operations**: Use Docker contexts (Docker API over SSH tunnel)
- **Stack Operations**: Use direct SSH (filesystem access + Docker CLI commands)

**Why SSH for Stack Management?**
Docker contexts cannot access remote filesystem paths. Stack deployment requires:
- Compose file transfer to remote host
- Directory creation and file persistence
- Remote docker compose command execution

## Consolidated Action-Parameter Pattern

This project uses the **Consolidated Action-Parameter Pattern** instead of simple `@mcp.tool()` decorators. This architectural choice is correct for complex Docker management operations.

### Why This Pattern?

Docker infrastructure management requires sophisticated capabilities that simple decorators cannot provide:

1. **Complex Multi-Step Operations**: Operations like migration require orchestration:
   - `migrate_stack`: stop → verify → archive → transfer → deploy → validate
   - `cleanup`: analyze → confirm → execute → verify
   - `deploy`: validate → pull → configure → start → health-check

2. **Hybrid Connection Model**: Different operations need different approaches:
   - **Container operations**: Docker contexts (API over SSH tunnel) for efficiency
   - **Stack operations**: Direct SSH (filesystem access) for compose file management

3. **Stateful Resource Management**:
   - Connection pooling and context caching
   - Resource lifecycle management
   - Cross-cutting concerns (logging, validation, error handling)

4. **Token Efficiency**: 
   - **2.6x more efficient**: 3 consolidated tools use ~5k tokens vs 27 individual tools using ~9.7k tokens
   - Each tool adds ~400-500 tokens - consolidation reduces this multiplicatively

### Implementation Pattern

```python
# CORRECT: Consolidated Action-Parameter Pattern with Service Delegation
class DockerMCPServer:
    def __init__(self, config):
        self.context_manager = DockerContextManager(config)
        # Service delegation for complex multi-step operations
        self.host_service = HostService(config, self.context_manager)
        self.container_service = ContainerService(config, self.context_manager)
        self.stack_service = StackService(config, self.context_manager)

    # MCP tools use consolidated action pattern
    async def docker_hosts(self, action: Literal["list", "add", "ports", ...], **kwargs):
        """Consolidated Docker hosts management."""
        # Route to appropriate service method based on action
        return await self.host_service.handle_action(action, **kwargs)
        
    async def docker_container(self, action: Literal["list", "start", "stop", ...], **kwargs):
        """Consolidated Docker container management."""
        return await self.container_service.handle_action(action, **kwargs)
        
    async def docker_compose(self, action: Literal["list", "deploy", "up", ...], **kwargs):
        """Consolidated Docker Compose stack management."""
        return await self.stack_service.handle_action(action, **kwargs)
```

### Service Layer Benefits

- **Services**: Business logic, validation, ToolResult formatting, complex orchestration
- **Tools**: Direct Docker/SSH operations, data structures, low-level implementation
- **Models**: Pydantic validation and type safety

**Why NOT Simple Decorators:**
Simple `@mcp.tool()` decorators work for stateless operations, but Docker infrastructure management requires orchestration, state management, and service composition that only the consolidated pattern can provide.

## Transfer Architecture Pattern

### Modular Transfer System
Migration operations use a pluggable transfer architecture for optimal performance and compatibility:

```python
# Abstract base for all transfer methods
class BaseTransfer(ABC):
    @abstractmethod
    async def transfer(self, source_host, target_host, source_path, target_path, **kwargs)
    @abstractmethod
    async def validate_requirements(self, host)
    @abstractmethod
    def get_transfer_type(self) -> str

# Concrete implementations
class RsyncTransfer(BaseTransfer):    # Universal rsync compatibility
class ArchiveUtils:                   # Tar/gzip with intelligent exclusions
```

### Transfer Method Selection
```python
# Migration manager uses rsync for universal compatibility
class MigrationManager:
    async def choose_transfer_method(self, source_host, target_host):
        return "rsync", self.rsync_transfer
```

**Transfer Method:**
- **Rsync** - Universal compatibility for all Docker environments


## Common Development Commands

```bash
# Development setup
uv sync --dev                              # Install with dev dependencies
uv run docker-mcp --config config/dev-hosts.yml  # Start dev server

# Testing (FastMCP in-memory pattern)
uv run pytest                             # Run all tests
uv run pytest -k "not slow"              # Skip slow tests (port scanning)
uv run pytest -m integration             # Integration tests only
uv run pytest --cov=docker_mcp           # With coverage

# Transfer module testing
uv run pytest tests/test_migration.py    # Migration and transfer tests
uv run pytest -k "migration"             # All migration-related tests
uv run pytest -k "rsync"                 # Rsync transfer tests

# Code quality
uv run ruff format .                      # Format code
uv run ruff check . --fix               # Lint and fix
uv run mypy docker_mcp/                  # Type checking
```

## Error Handling Patterns

### Modern Async Exception Handling

```python
# Custom exception hierarchy
class DockerMCPError(Exception):
    """Base exception for Docker MCP operations"""
    
class DockerContextError(DockerMCPError):
    """Docker context operation failed"""
    
class DockerCommandError(DockerMCPError):
    """Docker command execution failed"""

# Modern Python 3.11+ async exception patterns
async def complex_docker_operation():
    try:
        # Use asyncio.timeout for all operations (Python 3.11+)
        async with asyncio.timeout(30.0):
            result = await docker_operation()
    except* (DockerError, SSHError) as eg:  # Exception groups (Python 3.11+)
        for error in eg.exceptions:
            logger.error("Operation failed", error=str(error))
        raise
    except TimeoutError:
        logger.error("Operation timed out after 30 seconds")
        raise DockerMCPError("Docker operation timed out")

# Service validation pattern
def _validate_host(self, host_id: str) -> tuple[bool, str]:
    if host_id not in self.config.hosts:
        return False, f"Host '{host_id}' not found"
    return True, ""

# Batch operation error aggregation
async def batch_operation_with_error_groups(operations: list[Operation]):
    """Execute multiple operations with proper error aggregation."""
    errors: list[Exception] = []
    results: list[OperationResult] = []
    
    async with asyncio.TaskGroup() as tg:  # Python 3.11+
        tasks = [tg.create_task(execute_operation(op)) for op in operations]
    
    # Handle aggregated results with exception groups
    for task, operation in zip(tasks, operations):
        try:
            results.append(await task)
        except* (DockerError, SSHError) as eg:
            errors.extend(eg.exceptions)
            logger.error("Batch operation partial failure", 
                        operation=operation.name, 
                        errors=[str(e) for e in eg.exceptions])
    
    return BatchResult(results=results, errors=errors)
```

## Security Considerations

### Docker Command Validation

```python
import subprocess
from pathlib import Path

# Comprehensive allowed Docker commands
ALLOWED_DOCKER_COMMANDS = frozenset({
    "ps", "logs", "start", "stop", "restart", "stats",
    "compose", "pull", "build", "inspect", "images",
    "version", "info", "system", "volume", "network"
})

def validate_docker_command(cmd: list[str]) -> None:
    """Validate Docker command for security before execution."""
    if not cmd or not isinstance(cmd, list):
        raise SecurityError("Command must be a non-empty list")
    
    if cmd[0] not in ALLOWED_DOCKER_COMMANDS:
        raise SecurityError(f"Command not allowed: {cmd[0]}")
    
    # Additional validation for specific commands
    if cmd[0] == "system" and len(cmd) > 1:
        allowed_system_commands = {"df", "info", "events"}
        if cmd[1] not in allowed_system_commands:
            raise SecurityError(f"System subcommand not allowed: {cmd[1]}")

# Secure subprocess execution pattern
async def execute_docker_command(cmd: list[str], timeout: float = 30.0) -> CommandResult:
    """Execute Docker command with proper security and timeout."""
    # Validate command first
    validate_docker_command(cmd)
    
    try:
        # Use proper security annotations for legitimate subprocess calls
        result = subprocess.run(  # nosec B603 - validated Docker command
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout  # Always use timeouts to prevent hanging
        )
        return CommandResult(
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode
        )
    except subprocess.TimeoutExpired:
        raise DockerMCPError(f"Docker command timed out after {timeout} seconds")
    except subprocess.CalledProcessError as e:
        raise DockerCommandError(f"Docker command failed: {e}")
```

### SSH Security

- **SSH Key Authentication Only**: No passwords, only SSH keys from host configuration
- **Dedicated SSH Keys**: Use separate keys for Docker MCP, isolated from personal keys
- **Security Annotations**: SSH operations marked with `# nosec B603` for legitimate subprocess calls
- **Structured Logging**: All operations logged with host_id context for audit trails
- **Connection Timeouts**: All SSH operations have mandatory timeouts to prevent hanging
- **Host Key Verification**: StrictHostKeyChecking disabled only for automation, with logging

```python
# SSH command execution with security best practices
def build_ssh_command(host: DockerHost, remote_command: list[str]) -> list[str]:
    """Build secure SSH command with proper options."""
    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",  # Required for automation
        "-o", "UserKnownHostsFile=/dev/null",  # Prevent host key issues
        "-o", "LogLevel=ERROR",  # Reduce noise
        "-o", f"ConnectTimeout=10",  # Connection timeout
        "-o", f"ServerAliveInterval=30",  # Keep connection alive
    ]
    
    if host.identity_file:
        ssh_cmd.extend(["-i", host.identity_file])
    
    ssh_cmd.append(f"{host.user}@{host.hostname}")
    ssh_cmd.extend(remote_command)
    
    return ssh_cmd
```

### Migration Safety Patterns

```python
# ALWAYS stop containers by default (data integrity protection)
async def migrate_stack(
    self,
    source_host_id: str,
    target_host_id: str,
    stack_name: str,
    skip_stop_source: bool = False,  # Must explicitly skip stopping (DANGEROUS)
    # ... other params
):
    # Container verification before archiving
    if not skip_stop_source and not dry_run:
        # 1. Stop the stack
        stop_result = await self.stack_tools.manage_stack(source_host_id, stack_name, "down")
        
        # 2. Verify ALL containers are actually stopped
        verify_cmd = ["docker", "ps", "--filter", f"label=com.docker.compose.project={stack_name}"]
        if running_containers := check_result.stdout.strip():
            raise MigrationError(f"Containers still running: {running_containers}")
        
        # 3. Wait for filesystem sync
        await asyncio.sleep(2)
    
    # 4. Archive integrity verification
    verify_cmd = ["tar", "tzf", archive_path, ">/dev/null", "2>&1", "&&", "echo", "OK"]
    if "FAILED" in verify_result.stdout:
        raise MigrationError("Archive integrity check failed")
```

**Safety Principles:**
- **Default to Safe**: Containers stopped by default unless explicitly skipped
- **Verify State**: Confirm containers are completely stopped before archiving
- **Data Integrity**: Archive verification before transfer
- **Atomic Operations**: Use rsync for reliable data transfers
- **Rollback Capability**: Maintain source until target is verified

## Configuration Hierarchy

1. Command line arguments (`--config`)
2. Project config (`config/hosts.yml`)
3. User config (`~/.config/docker-mcp/hosts.yml`)
4. Docker context discovery (automatic)
5. Environment variables (legacy)

### Host Configuration

```yaml
hosts:
  production-1:
    hostname: server.example.com
    user: dockeruser
    appdata_path: "/opt/appdata"         # Container data storage path
    
  staging-host:
    hostname: staging.example.com
    user: dockeruser  
    appdata_path: "/opt/appdata"         # Standard filesystem
```

**Migration Behavior:**
- **Universal rsync transfer** for all host environments
- **Directory synchronization** with compression and delta transfers
- **Preserves all data** including permissions and timestamps

## Testing Conventions

```python
# FastMCP in-memory testing pattern
@pytest.mark.asyncio
async def test_list_containers(client: Client, test_host_id: str):
    result = await client.call_tool("list_containers", {"host_id": test_host_id})
    assert result.data["success"] is True
```

**Test Organization:**
- `@pytest.mark.unit` - Fast unit tests
- `@pytest.mark.integration` - Real Docker host tests  
- `@pytest.mark.slow` - Tests >10 seconds (port scanning)
- 85% minimum code coverage required

## Code Standards

### Modern Type Hinting (Python 3.10+)

```python
# CORRECT: Modern Python 3.10+ union syntax
config: dict[str, Any] | None = None
result: list[ContainerInfo] | None = None
hosts: dict[str, DockerHost] = {}
operation_result: OperationResult[ContainerInfo] | None = None

# Type aliases for complex types (Python 3.12+)
from typing import TypeAlias

HostId: TypeAlias = str
ContainerId: TypeAlias = str
StackName: TypeAlias = str
ResourceDict: TypeAlias = dict[str, Any]

# Generic types with modern syntax
from typing import TypeVar, Generic

T = TypeVar('T')

class OperationResult(BaseModel, Generic[T]):
    """Generic operation result with type safety."""
    success: bool
    data: T | None = None
    error: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)

# Usage with type safety
container_result: OperationResult[ContainerInfo] = await get_container_info(host_id, container_id)
stack_list: list[StackInfo] = await list_stacks(host_id)

# AVOID: Legacy Union syntax (pre-Python 3.10)
from typing import Union, Optional  # Don't use these anymore
config: Union[dict, None] = None  # Use dict | None instead
result: Optional[list] = None     # Use list | None instead
```

- **Type Hints**: Mandatory Python 3.10+ union syntax (`str | None` not `Optional[str]`)
- **Type Aliases**: Use `TypeAlias` for complex recurring types (Python 3.12+)
- **Pydantic Models**: All data structures with `Field(default_factory=list)`
- **Generic Types**: Use modern `Generic[T]` syntax with union types
- **Structured Logging**: `structlog` with context (host_id, operation)
- **Async/Await**: All I/O operations must be async

### Transfer Module Development

When adding new transfer methods, follow this pattern:

```python
from docker_mcp.core.transfer.base import BaseTransfer

class NewTransfer(BaseTransfer):
    async def transfer(self, source_host, target_host, source_path, target_path, **kwargs):
        """Implement the transfer logic"""
        # Validate requirements first
        await self.validate_requirements(source_host)
        await self.validate_requirements(target_host)
        
        # Perform transfer with error handling
        try:
            # Transfer implementation here
            return {"success": True, "stats": {...}}
        except Exception as e:
            raise TransferError(f"Transfer failed: {str(e)}")
    
    async def validate_requirements(self, host):
        """Check if host supports this transfer method"""
        # Implementation-specific validation
        
    def get_transfer_type(self) -> str:
        return "new_method"  # Unique identifier
```

**Register in migration.py:**
```python
# Add to MigrationManager.__init__()
self.new_transfer = NewTransfer()

# Add to choose_transfer_method()
if source_host.supports_new_method and target_host.supports_new_method:
    return "new_method", self.new_transfer
```
