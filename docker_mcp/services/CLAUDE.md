# Services Layer - Development Memory

## Service Layer Architecture

### Business Logic Separation Pattern
```python
class ContainerService:
    """Service for Docker container management operations."""
    
    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager
        self.container_tools = ContainerTools(config, context_manager)  # Tool delegation
        self.logger = structlog.get_logger()
```

### Service Dependencies
- **Config**: `DockerMCPConfig` for host configurations
- **Context Manager**: `DockerContextManager` for Docker operations
- **Tool Classes**: Corresponding tool classes for actual operations
- **Structured Logger**: `structlog` for consistent logging

## Tool Integration Pattern

### Delegation to Tool Classes
```python
# Service layer provides business logic and formatting
class ContainerService:
    async def list_containers(self, host_id: str, all_containers: bool = False):
        # Use tool class for actual Docker operations
        result = await self.container_tools.list_containers(host_id, all_containers)
        
        # Service handles formatting and user experience
        summary_lines = self._format_container_summary(result)
        
        return ToolResult(
            content=[TextContent(type="text", text="\n".join(summary_lines))],
            structured_content=result  # Raw data for programmatic access
        )
```

### Service vs Tool Responsibilities
- **Services**: Business logic, validation, formatting, error handling, user experience
- **Tools**: Direct Docker operations, raw data processing, technical implementation

## Validation Pattern

### Common Host Validation
```python
def _validate_host(self, host_id: str) -> tuple[bool, str]:
    """Validate host exists in configuration."""
    if host_id not in self.config.hosts:
        return False, f"Host '{host_id}' not found"
    return True, ""

# Usage in service methods
async def some_operation(self, host_id: str):
    is_valid, error_msg = self._validate_host(host_id)
    if not is_valid:
        return ToolResult(
            content=[TextContent(type="text", text=f"Error: {error_msg}")],
            structured_content={"success": False, "error": error_msg}
        )
```

### Validation Best Practices
- Always validate inputs before processing
- Return consistent error format (ToolResult)
- Use tuple returns for validation methods: `(is_valid: bool, error_msg: str)`
- Validate early, fail fast

## ToolResult Pattern

### Dual Content Strategy
```python
return ToolResult(
    content=[TextContent(type="text", text=user_friendly_message)],  # Human-readable
    structured_content={                                            # Machine-readable
        "success": True,
        "host_id": host_id,
        "data": processed_data,
        "metadata": additional_info
    }
)
```

### Success vs Error Handling
```python
# Success case
if result["success"]:
    return ToolResult(
        content=[TextContent(type="text", text=f"Success: {result['message']}")],
        structured_content=result
    )
else:
    return ToolResult(
        content=[TextContent(type="text", text=f"Error: {result['error']}")],
        structured_content=result
    )
```

## Formatting Pattern

### User-Friendly Output
```python
def _format_container_summary(self, container: dict[str, Any]) -> list[str]:
    """Format container information for display."""
    status_indicator = "●" if container["state"] == "running" else "○"
    ports_info = f" | Ports: {', '.join(container['ports'])}" if container["ports"] else ""
    
    return [
        f"{status_indicator} {container['name']} ({container['id']})",
        f"    Image: {container['image']}",
        f"    Status: {container['status']}{ports_info}"
    ]
```

### Formatting Method Naming
- `_format_*_summary`: Brief overview format
- `_format_*_details`: Detailed information format
- `_format_*_list`: List/table format
- All formatting methods are private (`_`) and return `list[str]`

## Error Handling Pattern

### Service-Level Exception Handling
```python
async def service_method(self, host_id: str) -> ToolResult:
    try:
        # Validation
        is_valid, error_msg = self._validate_host(host_id)
        if not is_valid:
            return self._error_result(error_msg)
            
        # Business logic
        result = await self.tool_class.some_operation(host_id)
        
        # Success formatting
        return self._success_result(result)
        
    except Exception as e:
        self.logger.error("Operation failed", host_id=host_id, error=str(e))
        return ToolResult(
            content=[TextContent(type="text", text=f"❌ Operation failed: {str(e)}")],
            structured_content={"success": False, "error": str(e), "host_id": host_id}
        )
```

### Error Message Conventions
- Use ❌ emoji for errors in user-facing messages
- Include contextual information (host_id, operation type)
- Log errors with structured data
- Return consistent error structure

## Logging Pattern

### Structured Logging with Context
```python
self.logger.info(
    "Operation completed",
    host_id=host_id,
    operation="container_start",
    duration=time.time() - start_time
)

self.logger.error(
    "Operation failed",
    host_id=host_id,
    container_id=container_id,
    error=str(e)
)
```

## Service Import Pattern

### Centralized Service Exports
```python
# __init__.py
from .config import ConfigService
from .container import ContainerService
from .host import HostService
from .stack import StackService

__all__ = [
    "HostService",
    "ContainerService", 
    "StackService",
    "ConfigService",
]
```

## Common Service Methods

### Standard Method Signatures
- `async def list_*` - List resources with pagination
- `async def get_*_info` - Get detailed information about single resource
- `async def manage_*` - Unified lifecycle management (start/stop/restart)
- `async def deploy_*` - Deploy/create resources
- `def _validate_*` - Input validation helpers
- `def _format_*` - Output formatting helpers

## Configuration Pattern

### Service Configuration Access
```python
class SomeService:
    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config  # Always store config reference
        
    def get_host_config(self, host_id: str) -> DockerHost | None:
        """Get host configuration safely."""
        return self.config.hosts.get(host_id)
```

## Service Testing Pattern

### In-Memory Testing Support
```python
# Services work with in-memory configuration
# No file system dependencies for unit testing
config = DockerMCPConfig()
config.hosts["test-host"] = DockerHost(hostname="test.local", user="testuser")

service = ContainerService(config, mock_context_manager)
```

## Service Composition

### Service Layer in Server
```python
# server.py
class DockerMCPServer:
    def __init__(self, config):
        self.context_manager = DockerContextManager(config)
        
        # Initialize all services
        self.host_service = HostService(config)
        self.container_service = ContainerService(config, self.context_manager)
        self.stack_service = StackService(config, self.context_manager)
        self.config_service = ConfigService(config, self.context_manager)
```

Services provide the business logic layer between MCP tools (user interface) and core modules (technical implementation), ensuring clean separation of concerns and consistent user experience across all operations.