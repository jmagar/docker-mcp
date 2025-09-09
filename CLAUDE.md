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
class ZFSTransfer(BaseTransfer):      # Block-level ZFS send/receive
class RsyncTransfer(BaseTransfer):    # Universal rsync compatibility
class ArchiveUtils:                   # Tar/gzip with intelligent exclusions
```

### Automatic Method Selection
```python
# Migration manager chooses optimal transfer method
class MigrationManager:
    async def choose_transfer_method(self, source_host, target_host):
        if source_host.zfs_capable and target_host.zfs_capable:
            return "zfs", self.zfs_transfer
        return "rsync", self.rsync_transfer
```

**Transfer Method Priority:**
1. **ZFS send/receive** - When both hosts have `zfs_capable: true`
2. **Rsync fallback** - Universal compatibility for mixed environments

### ZFS Integration Patterns

```python
# ZFS capability detection
class ZFSTransfer:
    async def detect_zfs_capability(self, host, appdata_path):
        # Verify ZFS availability and dataset existence
        zfs_check = await self._run_ssh_command(host, ["zfs", "list", dataset])
        return zfs_check.returncode == 0
    
    async def create_snapshot(self, host, dataset, snapshot_name):
        # Create atomic snapshot for consistent backups
        cmd = ["zfs", "snapshot", f"{dataset}@{snapshot_name}"]
        return await self._run_ssh_command(host, cmd)
```

### ZFS Dataset Auto-Creation

```python
# Automatic dataset creation for services
class ZFSTransfer:
    async def ensure_service_dataset_exists(self, host, service_path):
        """Ensure a service path exists as a ZFS dataset.
        
        - Checks if dataset already exists
        - Converts existing directory to dataset (preserving data)
        - Creates empty dataset if neither exists
        """
        service_name = service_path.split("/")[-1]
        expected_dataset = f"{host.zfs_dataset}/{service_name}"
        
        # Auto-convert directories to datasets safely
        if directory_exists and not dataset_exists:
            await self._convert_directory_to_dataset(host, service_path, expected_dataset)
        
        return expected_dataset
    
    async def transfer_multiple_services(self, source_host, target_host, service_paths):
        """Transfer multiple service datasets with auto-creation.
        
        - Automatically creates datasets on both source and target
        - Groups services by dataset to avoid duplicate transfers
        - Handles mixed directory/dataset scenarios safely
        """
        for service_path in service_paths:
            # Ensure datasets exist on both sides
            source_dataset = await self.ensure_service_dataset_exists(source_host, service_path)
            target_dataset = await self.ensure_service_dataset_exists(target_host, target_service_path)
            
            # Transfer the dataset
            await self.transfer(source_host, target_host, source_path, target_path,
                              source_dataset=source_dataset, target_dataset=target_dataset)
```

**ZFS Benefits:**
- Block-level transfers (faster for large datasets)
- Atomic snapshots (crash-consistent backups)
- Property preservation (permissions, timestamps, metadata)
- **Auto-dataset creation** (no manual ZFS setup required)
- **Directory-to-dataset conversion** (preserves existing data)
- Incremental send/receive support (future enhancement)

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
uv run pytest -k "transfer"              # Transfer module tests

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
- **Atomic Operations**: Use snapshots and atomic transfers where possible
- **Rollback Capability**: Maintain source until target is verified

## Configuration Hierarchy

1. Command line arguments (`--config`)
2. Project config (`config/hosts.yml`)
3. User config (`~/.config/docker-mcp/hosts.yml`)
4. Docker context discovery (automatic)
5. Environment variables (legacy)

### ZFS Configuration

```yaml
hosts:
  production-1:
    hostname: server.example.com
    user: dockeruser
    appdata_path: "/tank/appdata"        # ZFS dataset mount point
    zfs_capable: true                    # Enable ZFS transfers
    zfs_dataset: "tank/appdata"          # ZFS dataset for send/receive
    
  legacy-host:
    hostname: old-server.example.com
    user: dockeruser  
    appdata_path: "/opt/appdata"         # Standard filesystem
    zfs_capable: false                   # Will use rsync fallback
```

**ZFS Auto-Detection & Dataset Management:**
- System automatically detects ZFS availability if `zfs_capable: true`
- **Auto-creates service datasets** during migration (no manual setup required)
- **Safely converts directories to datasets** while preserving existing data
- Falls back to rsync if ZFS detection fails
- Migration chooses optimal method based on both source AND target capabilities

**ZFS Dataset Patterns:**
```bash
# Automatic dataset structure created by migration
rpool/appdata                    # Parent dataset (configured in hosts.yml)
├── rpool/appdata/authelia       # Service dataset (auto-created)
├── rpool/appdata/plex          # Service dataset (auto-created)
├── rpool/appdata/jellyfin      # Service dataset (auto-created)
└── ...                         # Additional services as separate datasets
```

**Migration Behavior:**
- **Existing ZFS datasets**: Used directly for efficient ZFS send/receive
- **Existing directories**: Safely converted to ZFS datasets with data preservation
- **New services**: Empty ZFS datasets created automatically
- **Multi-service stacks**: Each service component gets its own dataset

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
