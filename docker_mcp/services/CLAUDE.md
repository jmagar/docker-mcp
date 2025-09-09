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

## Modern Validation Patterns (Python 3.10+)

### Pydantic v2 Input Validation
```python
from pydantic import BaseModel, Field, ValidationError, validator
from typing import Literal, Annotated
import re

class ContainerOperationRequest(BaseModel):
    """Modern validation model for container operations."""
    host_id: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")]
    container_id: Annotated[str, Field(min_length=1, max_length=128)]
    action: Literal["start", "stop", "restart", "pause", "unpause", "remove"]
    timeout: Annotated[int, Field(ge=1, le=300)] = 30
    force: bool = False
    
    @validator('host_id')
    @classmethod
    def validate_host_exists(cls, v, values, **kwargs):
        # Can access service context through custom validation
        if hasattr(kwargs.get('context'), 'config'):
            config = kwargs['context'].config
            if v not in config.hosts:
                raise ValueError(f"Host '{v}' not found in configuration")
        return v

class StackDeployRequest(BaseModel):
    """Validation for stack deployment operations."""
    host_id: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_-]+$")
    stack_name: str = Field(min_length=1, max_length=63, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    compose_content: str = Field(min_length=10)  # Must have actual content
    environment: dict[str, str] = Field(default_factory=dict)
    pull_images: bool = True
    recreate: bool = False
    
    @validator('stack_name')
    @classmethod  
    def validate_stack_name_security(cls, v):
        """Ensure stack name is safe for filesystem operations."""
        reserved_names = {"docker", "compose", "system", "network", "volume"}
        if v.lower() in reserved_names:
            raise ValueError(f"Stack name '{v}' is reserved")
        return v
    
    @validator('environment')
    @classmethod
    def validate_no_sensitive_env(cls, v):
        """Check for accidentally exposed secrets in environment."""
        sensitive_patterns = [r"password", r"secret", r"token", r"key"]
        for key, value in v.items():
            key_lower = key.lower()
            if any(re.search(pattern, key_lower) for pattern in sensitive_patterns):
                # Allow but warn - don't block legitimate use
                continue
        return v

# Modern validation in service methods
async def deploy_stack_validated(
    self,
    request: StackDeployRequest
) -> ToolResult:
    """Type-safe stack deployment with automatic validation."""
    try:
        # Pydantic automatically validates all fields
        # Additional business logic validation
        await self._validate_host_connectivity(request.host_id)
        await self._validate_compose_syntax(request.compose_content)
        
        return await self._execute_deploy(request)
        
    except ValidationError as e:
        # Detailed validation error reporting
        validation_errors = [f"{err['loc'][0]}: {err['msg']}" for err in e.errors()]
        return self._validation_error_result(validation_errors)
    
    except Exception as e:
        return self._unexpected_error_result(e)
```

### Advanced Validation Patterns
```python
from typing import TypeGuard, Any
import asyncio
from functools import wraps

def validate_host_exists(func):
    """Decorator for automatic host validation."""
    @wraps(func)
    async def wrapper(self, host_id: str, *args, **kwargs):
        if not self._is_valid_host(host_id):
            return self._error_result(f"Host '{host_id}' not found")
        return await func(self, host_id, *args, **kwargs)
    return wrapper

def validate_container_id(func):
    """Decorator for container ID validation."""
    @wraps(func)
    async def wrapper(self, host_id: str, container_id: str, *args, **kwargs):
        if not self._is_valid_container_id(container_id):
            return self._error_result(f"Invalid container ID: {container_id}")
        return await func(self, host_id, container_id, *args, **kwargs)
    return wrapper

# Modern type guard validation
def is_docker_host_config(obj: Any) -> TypeGuard[DockerHost]:
    """Type guard for Docker host configuration."""
    return (
        hasattr(obj, 'hostname') and isinstance(obj.hostname, str) and
        hasattr(obj, 'user') and isinstance(obj.user, str) and
        hasattr(obj, 'ssh_port') and isinstance(obj.ssh_port, int)
    )

# Validation with Result pattern (modern error handling)
from typing import Generic, TypeVar, Union
from enum import Enum

T = TypeVar('T')

class ValidationResult(Generic[T]):
    """Result pattern for validation with detailed error context."""
    
    def __init__(self, value: T | None = None, errors: list[str] | None = None):
        self.value = value
        self.errors = errors or []
        self.is_valid = value is not None and not errors
    
    def add_error(self, error: str) -> None:
        self.errors.append(error)
        self.is_valid = False

async def validate_deployment_requirements(
    self,
    host_id: str,
    stack_name: str,
    compose_content: str
) -> ValidationResult[StackDeployRequest]:
    """Comprehensive async validation with detailed results."""
    result: ValidationResult[StackDeployRequest] = ValidationResult()
    
    # Parallel validation checks
    async with asyncio.TaskGroup() as tg:
        host_task = tg.create_task(self._check_host_connectivity(host_id))
        port_task = tg.create_task(self._check_port_conflicts(host_id, compose_content))
        syntax_task = tg.create_task(self._validate_compose_syntax(compose_content))
    
    # Process validation results
    if not await host_task:
        result.add_error(f"Cannot connect to host '{host_id}'")
    
    port_conflicts = await port_task
    if port_conflicts:
        result.add_error(f"Port conflicts detected: {', '.join(map(str, port_conflicts))}")
    
    syntax_errors = await syntax_task
    if syntax_errors:
        result.add_error(f"Compose syntax errors: {'; '.join(syntax_errors)}")
    
    # If all validation passes, create the request model
    if result.is_valid:
        try:
            result.value = StackDeployRequest(
                host_id=host_id,
                stack_name=stack_name,
                compose_content=compose_content
            )
        except ValidationError as e:
            for error in e.errors():
                result.add_error(f"{error['loc'][0]}: {error['msg']}")
    
    return result
```

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

## Async Context Managers (Python 3.7+)

### Resource Management with Context Managers
```python
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncContextManager, AsyncGenerator
import time

class ServiceOperationContext:
    """Context for tracking service operations with automatic cleanup."""
    
    def __init__(self, host_id: str, operation: str):
        self.host_id = host_id
        self.operation = operation
        self.start_time = time.perf_counter()
        self.logger = structlog.get_logger()
        self.resources: list[Any] = []
    
    async def __aenter__(self):
        await self.logger.ainfo(
            "Service operation started",
            host_id=self.host_id,
            operation=self.operation
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        duration = time.perf_counter() - self.start_time
        
        # Cleanup resources in reverse order
        for resource in reversed(self.resources):
            if hasattr(resource, 'close'):
                await resource.close()
        
        if exc_type:
            await self.logger.aerror(
                "Service operation failed",
                host_id=self.host_id,
                operation=self.operation,
                error=str(exc_val),
                duration_seconds=duration
            )
        else:
            await self.logger.ainfo(
                "Service operation completed",
                host_id=self.host_id,
                operation=self.operation,
                duration_seconds=duration
            )
    
    def add_resource(self, resource: Any):
        """Track a resource for automatic cleanup."""
        self.resources.append(resource)

@asynccontextmanager
async def docker_operation_context(
    self,
    host_id: str,
    operation: str
) -> AsyncGenerator[ServiceOperationContext, None]:
    """Context manager for Docker operations with automatic resource management."""
    async with ServiceOperationContext(host_id, operation) as ctx:
        # Acquire Docker connection
        connection = await self.context_manager.get_connection(host_id)
        ctx.add_resource(connection)
        
        # Set up operation timeout
        async with asyncio.timeout(300.0):  # 5 minute default timeout
            yield ctx

# Usage in service methods
async def deploy_stack_with_context(
    self,
    host_id: str,
    stack_name: str,
    compose_content: str
) -> ToolResult:
    """Deploy stack using async context manager for resource management."""
    
    async with self.docker_operation_context(host_id, "deploy_stack") as ctx:
        # All operations within this context are automatically logged and cleaned up
        
        # Validate deployment requirements
        validation_result = await self._validate_deployment_requirements(
            host_id, stack_name, compose_content
        )
        
        if not validation_result.is_valid:
            return self._validation_error_result(validation_result.errors)
        
        # Deploy with automatic resource management
        deploy_result = await self._execute_deployment(
            ctx, validation_result.value
        )
        
        return self._success_result(deploy_result)
        # Context manager automatically handles cleanup and logging
```

### Batch Operations with Context Management
```python
@asynccontextmanager
async def batch_operation_context(
    self,
    operation_name: str,
    operations: list[dict[str, Any]]
) -> AsyncGenerator[dict[str, Any], None]:
    """Context manager for batch operations with progress tracking."""
    
    batch_id = f"batch_{int(time.time())}"
    progress = {
        "batch_id": batch_id,
        "total": len(operations),
        "completed": 0,
        "failed": 0,
        "results": [],
        "errors": []
    }
    
    await self.logger.ainfo(
        "Batch operation started",
        operation=operation_name,
        batch_id=batch_id,
        total_operations=len(operations)
    )
    
    try:
        yield progress
    finally:
        # Always log batch completion stats
        await self.logger.ainfo(
            "Batch operation completed",
            operation=operation_name,
            batch_id=batch_id,
            completed=progress["completed"],
            failed=progress["failed"],
            success_rate=progress["completed"] / len(operations) if operations else 0
        )

async def batch_container_operations(
    self,
    operations: list[dict[str, Any]]
) -> ToolResult:
    """Execute multiple container operations with progress tracking."""
    
    async with self.batch_operation_context("container_batch", operations) as progress:
        # Use TaskGroup for concurrent execution with proper error handling
        async with asyncio.TaskGroup() as tg:
            tasks = []
            
            for op in operations:
                task = tg.create_task(
                    self._execute_single_container_operation(op, progress)
                )
                tasks.append(task)
        
        # Aggregate results
        results = []
        for task in tasks:
            try:
                result = await task
                results.append(result)
            except* Exception as eg:
                # Handle partial failures in batch
                for error in eg.exceptions:
                    progress["errors"].append(str(error))
                    progress["failed"] += 1
        
        return ToolResult(
            content=[TextContent(
                type="text", 
                text=f"Batch operation completed: {progress['completed']}/{progress['total']} successful"
            )],
            structured_content=progress
        )
```

### Connection Pooling with Context Managers
```python
from typing import Dict
import weakref

class ConnectionPool:
    """Async connection pool with automatic cleanup."""
    
    def __init__(self, max_connections: int = 10):
        self.max_connections = max_connections
        self.connections: Dict[str, Any] = {}
        self.connection_counts: Dict[str, int] = {}
        self._lock = asyncio.Lock()
    
    @asynccontextmanager
    async def get_connection(self, host_id: str) -> AsyncGenerator[Any, None]:
        """Get pooled connection with automatic reference counting."""
        
        async with self._lock:
            if host_id in self.connections:
                # Reuse existing connection
                connection = self.connections[host_id]
                self.connection_counts[host_id] += 1
            else:
                # Create new connection
                connection = await self._create_connection(host_id)
                self.connections[host_id] = connection
                self.connection_counts[host_id] = 1
        
        try:
            yield connection
        finally:
            # Decrease reference count
            async with self._lock:
                self.connection_counts[host_id] -= 1
                
                # Clean up unused connections
                if self.connection_counts[host_id] <= 0:
                    await self._cleanup_connection(host_id)
                    del self.connections[host_id]
                    del self.connection_counts[host_id]

# Service using connection pooling
class ContainerService:
    def __init__(self, config, context_manager):
        self.config = config
        self.context_manager = context_manager
        self.connection_pool = ConnectionPool()
    
    async def managed_container_operation(
        self,
        host_id: str,
        container_id: str,
        action: str
    ) -> ToolResult:
        """Container operation with pooled connection management."""
        
        async with self.connection_pool.get_connection(host_id) as connection:
            # Connection is automatically managed and reused
            result = await self._execute_container_action(
                connection, container_id, action
            )
            return self._format_result(result)
```

## Error Handling Pattern

### Modern Service-Level Exception Handling (Python 3.11+)
```python
async def service_method(self, host_id: str) -> ToolResult:
    """Service method with modern async error handling."""
    
    async with self.docker_operation_context(host_id, "service_operation") as ctx:
        try:
            # Validation with modern patterns
            validation_result = await self._validate_inputs(host_id)
            if not validation_result.is_valid:
                return self._validation_error_result(validation_result.errors)
            
            # Business logic with timeout protection
            async with asyncio.timeout(60.0):
                result = await self.tool_class.some_operation(host_id)
            
            return self._success_result(result)
            
        except* (DockerCommandError, DockerContextError) as eg:
            # Handle multiple related errors
            errors = [str(e) for e in eg.exceptions]
            await ctx.logger.aerror(
                "Multiple Docker errors",
                host_id=host_id,
                errors=errors
            )
            return self._multi_error_result(errors)
            
        except asyncio.TimeoutError:
            await ctx.logger.aerror(
                "Operation timeout",
                host_id=host_id,
                timeout_seconds=60.0
            )
            return ToolResult(
                content=[TextContent(type="text", text="❌ Operation timed out")],
                structured_content={
                    "success": False, 
                    "error": "timeout", 
                    "timeout_seconds": 60.0,
                    "host_id": host_id
                }
            )
            
        except Exception as e:
            await ctx.logger.aerror(
                "Unexpected service error",
                host_id=host_id,
                error=str(e),
                error_type=type(e).__name__
            )
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Operation failed: {str(e)}")],
                structured_content={
                    "success": False, 
                    "error": str(e), 
                    "error_type": type(e).__name__,
                    "host_id": host_id
                }
            )
```

### Error Message Conventions
- Use ❌ emoji for errors in user-facing messages
- Include contextual information (host_id, operation type)
- Use structured logging with async methods (`logger.aerror`)
- Return consistent error structure with detailed context
- Preserve exception chains for debugging

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
