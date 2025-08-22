# FastMCP Docker Context Manager - Project Memory

## Core Architecture

### Hybrid Connection Model
- **Container Operations**: Use Docker contexts (Docker API over SSH tunnel)
- **Stack Operations**: Use direct SSH (filesystem access + Docker CLI commands)

**Why SSH for Stack Management?**
Docker contexts cannot access remote filesystem paths. Stack deployment requires:
- Compose file transfer to remote host
- Directory creation and file persistence
- Remote docker-compose command execution

## Services Layer Pattern

```python
# Tools delegate to services for business logic
class DockerMCPServer:
    def __init__(self, config):
        self.context_manager = DockerContextManager(config)
        self.host_service = HostService(config)
        self.container_service = ContainerService(config, self.context_manager)
        self.stack_service = StackService(config, self.context_manager)

    # MCP tools delegate to services
    async def list_containers(self, host_id, ...):
        return await self.container_service.list_containers(host_id, ...)
```

**Service Responsibilities:**
- **Services**: Business logic, validation, ToolResult formatting
- **Tools**: Direct Docker/SSH operations, data structures
- **Models**: Pydantic validation and type safety

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

# Code quality
uv run ruff format .                      # Format code
uv run ruff check . --fix               # Lint and fix
uv run mypy docker_mcp/                  # Type checking
```

## Error Handling Patterns

```python
# Custom exception hierarchy
class DockerMCPError(Exception):
    """Base exception for Docker MCP operations"""
    
class DockerContextError(DockerMCPError):
    """Docker context operation failed"""
    
class DockerCommandError(DockerMCPError):
    """Docker command execution failed"""

# Service validation pattern
def _validate_host(self, host_id: str) -> tuple[bool, str]:
    if host_id not in self.config.hosts:
        return False, f"Host '{host_id}' not found"
    return True, ""
```

## Security Considerations

### Docker Command Validation
```python
# Allow only specific Docker commands
ALLOWED_COMMANDS = {
    "ps", "logs", "start", "stop", "restart", "stats",
    "compose", "pull", "build", "inspect", "images"
}

def _validate_docker_command(self, command: str) -> None:
    parts = command.strip().split()
    if not parts or parts[0] not in ALLOWED_COMMANDS:
        raise ValueError(f"Command not allowed: {parts[0] if parts else 'empty'}")
```

### SSH Security
- Use SSH keys from host configuration for all connections
- SSH operations marked with `# nosec B603` for legitimate subprocess calls
- Structured logging for all operations with host_id context

## Configuration Hierarchy

1. Command line arguments (`--config`)
2. Project config (`config/hosts.yml`)
3. User config (`~/.config/docker-mcp/hosts.yml`)
4. Docker context discovery (automatic)
5. Environment variables (legacy)

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

- **Type Hints**: Mandatory Python 3.10+ syntax (`str | None`)
- **Pydantic Models**: All data structures with `Field(default_factory=list)`
- **Structured Logging**: `structlog` with context (host_id, operation)
- **Async/Await**: All I/O operations must be async