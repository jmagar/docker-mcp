# GitHub Copilot Instructions

This repository implements a FastMCP Docker Context Manager for AI-powered Docker management across multiple hosts.

## Core Architecture Patterns

### Consolidated Action-Parameter Pattern
This project uses consolidated MCP tools instead of simple `@mcp.tool()` decorators for complex Docker operations:

```python
# ✅ Correct: Consolidated tools with action routing
async def docker_hosts(self, action: Literal["list", "add", "ports", ...], **kwargs):
    """Consolidated Docker hosts management."""
    return await self.host_service.handle_action(action, **kwargs)

async def docker_container(self, action: Literal["list", "start", "stop", ...], **kwargs):
    """Consolidated Docker container management."""
    return await self.container_service.handle_action(action, **kwargs)
```

**Why:** Docker infrastructure management requires orchestration, state management, and service composition that only the consolidated pattern provides. This is 2.6x more token-efficient than individual tools.

### Service Layer Architecture
- **Services**: Business logic, validation, ToolResult formatting, complex orchestration
- **Tools**: Direct Docker/SSH operations, data structures, low-level implementation  
- **Models**: Pydantic validation and type safety

### Hybrid Connection Model
- **Container Operations**: Docker contexts (API over SSH tunnel) for efficiency
- **Stack Operations**: Direct SSH (filesystem access) for compose file management

## Code Standards

### Modern Python 3.11+ Syntax
```python
# ✅ Use modern union syntax
config: dict[str, Any] | None = None
result: list[ContainerInfo] | None = None
operation_result: OperationResult[ContainerInfo] | None = None

# ✅ Type aliases for complex types  
from typing import TypeAlias
HostId: TypeAlias = str
ContainerId: TypeAlias = str

# ❌ Avoid legacy syntax
from typing import Union, Optional  # Don't use
config: Union[dict, None] = None   # Use dict | None instead
```

### Type Safety Requirements
- **Type Hints**: Mandatory Python 3.11+ union syntax (`str | None` not `Optional[str]`)
- **Pydantic Models**: All data structures with `Field(default_factory=list)`
- **Generic Types**: Use modern `Generic[T]` syntax with union types
- **Async/Await**: All I/O operations must be async

### Code Style (Ruff Configuration)
- 100-character line length, double quotes, space indentation
- `snake_case` for modules/functions/variables, `PascalCase` for classes, `UPPER_SNAKE` for constants
- Preserve service → tool → resource layering

## Security Patterns

### Docker Command Validation
```python
# Always validate Docker commands before execution
ALLOWED_DOCKER_COMMANDS = frozenset({
    "ps", "logs", "start", "stop", "restart", "stats",
    "compose", "pull", "build", "inspect", "images"
})

def validate_docker_command(cmd: list[str]) -> None:
    if cmd[0] not in ALLOWED_DOCKER_COMMANDS:
        raise SecurityError(f"Command not allowed: {cmd[0]}")
```

### SSH Security
- SSH key authentication only (no passwords)
- Security annotations: `# nosec B603 - validated Docker command`
- Connection timeouts on all SSH operations
- Structured logging with host_id context for audit trails

### Migration Safety
- Containers stopped by default unless explicitly skipped
- Verify state before archiving (containers fully stopped)
- Archive integrity verification before transfer
- Use rsync for reliable data transfers

## Testing Guidelines

### Test Organization
```python
# FastMCP in-memory testing pattern
@pytest.mark.asyncio
async def test_list_containers(client: Client, test_host_id: str):
    result = await client.call_tool("list_containers", {"host_id": test_host_id})
    assert result.data["success"] is True
```

**Markers:**
- `@pytest.mark.unit` - Fast unit tests
- `@pytest.mark.integration` - Real Docker host tests
- `@pytest.mark.slow` - Tests >10 seconds (port scanning)
- 85% minimum code coverage required

### Development Commands
```bash
# Setup
uv sync --dev                              # Install dependencies
uv run docker-mcp --config config/hosts.example.yml  # Start server

# Testing
uv run pytest                             # Run all tests
uv run pytest -k "not slow"              # Skip slow tests
uv run pytest --cov=docker_mcp           # With coverage

# Code quality
uv run ruff format .                      # Format code
uv run ruff check . --fix               # Lint and fix
uv run mypy docker_mcp/                  # Type checking
```

## Error Handling Patterns

### Modern Async Exception Handling (Python 3.11+)
```python
async def complex_docker_operation():
    try:
        async with asyncio.timeout(30.0):  # Python 3.11+
            result = await docker_operation()
    except* (DockerError, SSHError) as eg:  # Exception groups
        for error in eg.exceptions:
            logger.error("Operation failed", error=str(error))
        raise
    except TimeoutError:
        raise DockerMCPError("Docker operation timed out")
```

## Configuration Patterns

### Host Configuration Structure
```yaml
hosts:
  production-1:
    hostname: server.example.com
    user: dockeruser
    appdata_path: "/opt/appdata"     # Container data storage path
    description: "Production web server"
    tags: ["production", "web"]
    enabled: true
```

### Transfer Architecture
Migration operations use rsync for universal compatibility across all Docker environments with directory synchronization, compression, and delta transfers.

## Common Issues to Avoid

1. **Don't** use simple `@mcp.tool()` decorators - use consolidated action-parameter pattern
2. **Don't** use legacy Union/Optional syntax - use Python 3.11+ union syntax
3. **Don't** skip Docker command validation - always validate before subprocess execution
4. **Don't** use passwords for SSH - SSH keys only with proper security annotations
5. **Don't** forget timeouts on async operations - use `asyncio.timeout()` wrapper
6. **Don't** commit real host inventories or SSH keys - use example files only

## Repository Structure
- `docker_mcp/services/` - Business logic and orchestration
- `docker_mcp/tools/` - Direct Docker/SSH operations
- `docker_mcp/models/` - Pydantic data models
- `docker_mcp/core/` - Core infrastructure (contexts, transfers, etc.)
- `config/` - Configuration examples and templates
- `tests/` - Test files mirroring module structure