# Tools Layer - Development Memory

## Consolidated Action-Parameter Tool Architecture

### Why Consolidated Tools Pattern

The Docker MCP project uses **Consolidated Action-Parameter Pattern** instead of simple `@mcp.tool()` decorators. This is the CORRECT approach for complex Docker management operations.

**Why This Pattern Over Simple Decorators:**
- **Complex Multi-Step Operations**: Docker operations need orchestration and state management
- **Hybrid Connection Models**: Different operations require Docker API vs direct SSH
- **Service Composition**: Business logic requires dependency injection and service layers
- **Token Efficiency**: 3 consolidated tools vs 27 individual tools (2.6x more efficient)

### Server Method Pattern (CORRECT)

```python
class DockerMCPServer:
    """Server with methods that become MCP tools via FastMCP registration."""
    
    def __init__(self, config: DockerMCPConfig):
        self.config = config
        self.context_manager = DockerContextManager(config)
        # Service delegation for complex operations
        self.host_service = HostService(config, self.context_manager)
        self.container_service = ContainerService(config, self.context_manager)
        self.stack_service = StackService(config, self.context_manager)
    
    # This method becomes an MCP tool automatically via FastMCP
    async def docker_container(
        self,
        action: Literal["list", "info", "start", "stop", "restart", "logs", "stats"],
        host_id: str,
        container_id: str = "",
        **kwargs
    ) -> ToolResult:
        """Consolidated Docker container management.
        
        This single tool handles all container operations through action parameter.
        Each action routes to appropriate service methods with sub-actions and state management.
        """
        # Delegate to service layer for business logic and complex orchestration
        return await self.container_service.handle_action(action, host_id, container_id, **kwargs)
    
    async def docker_hosts(
        self,
        action: Literal["list", "add", "ports", "cleanup", "import_ssh", ...],
        host_id: str = "",
        **kwargs
    ) -> ToolResult:
        """Consolidated Docker hosts management."""
        return await self.host_service.handle_action(action, host_id, **kwargs)
        
    async def docker_compose(
        self,
        action: Literal["list", "deploy", "up", "down", "migrate", ...],
        host_id: str,
        stack_name: str = "",
        **kwargs
    ) -> ToolResult:
        """Consolidated Docker Compose stack management."""
        return await self.stack_service.handle_action(action, host_id, stack_name, **kwargs)
```

### Service Delegation Benefits (CORRECT for Complex Operations)

**Why Service Delegation Pattern:**

```python
# Service Layer Pattern handles complex orchestration
class ContainerService:
    """Service handles business logic for container operations."""
    
    async def handle_action(self, action: str, host_id: str, container_id: str, **kwargs):
        """Route action to appropriate handler with validation and state management."""
        # 1. Validate inputs and host existence
        is_valid, error_msg = self._validate_host(host_id)
        if not is_valid:
            return self._error_result(error_msg)
        
        # 2. Acquire resources (connections, locks) for stateful operations
        async with self._get_operation_context(host_id, container_id) as ctx:
            # 3. Execute action (potentially multi-step with sub-actions)
            if action == "start":
                return await self._start_container_with_validation(ctx, container_id, **kwargs)
            elif action == "migrate":
                # Complex multi-step operation requiring orchestration
                return await self._migrate_container_multi_step(ctx, container_id, **kwargs)
            elif action == "stop":
                # May require graceful shutdown, health checks, cleanup
                return await self._stop_container_gracefully(ctx, container_id, **kwargs)
            
        # 4. Format results for MCP client (ToolResult with both content and structured data)
        # 5. Handle errors consistently across all operations
    
    async def _migrate_container_multi_step(self, ctx, container_id: str, target_host: str, **kwargs):
        """Example of complex orchestration that simple decorators cannot handle."""
        try:
            # Step 1: Pre-migration validation
            await self._validate_migration_requirements(container_id, target_host)
            
            # Step 2: Create snapshot/backup
            backup_result = await self._create_container_backup(ctx, container_id)
            
            # Step 3: Stop container gracefully
            await self._graceful_stop_with_health_check(ctx, container_id)
            
            # Step 4: Transfer data (hybrid SSH + Docker context usage)
            await self._transfer_container_data(ctx, container_id, target_host)
            
            # Step 5: Deploy on target with verification
            deploy_result = await self._deploy_and_verify(target_host, container_id, backup_result)
            
            # Step 6: Cleanup source if successful
            if deploy_result.success:
                await self._cleanup_source_container(ctx, container_id)
            
            return ToolResult(
                content=[TextContent(type="text", text=f"✅ Container migration completed")],
                structured_content={
                    "success": True,
                    "migration_id": deploy_result.migration_id,
                    "source_host": ctx.host_id,
                    "target_host": target_host,
                    "steps_completed": 6
                }
            )
        except Exception as e:
            # Rollback logic for failed multi-step operations
            await self._rollback_partial_migration(ctx, container_id, step_reached)
            raise
```

### Architecture Layer Responsibilities

- **Services**: Business logic, validation, complex orchestration, state management, multi-step operations
- **Tool Classes**: Direct Docker/SSH operations, data structures, low-level implementation
- **Server Methods** (MCP Tools): Action routing, parameter validation, service delegation  
- **Simple Decorators** ❌: Only suitable for stateless, single-step operations (NOT applicable here)

### Tool Dependencies
- **Config**: `DockerMCPConfig` for host configurations and settings
- **Context Manager**: `DockerContextManager` for Docker command execution
- **Service Layer**: Business logic, validation, complex orchestration
- **Models**: Pydantic models for data validation and serialization
- **Structured Logger**: `structlog` for operation tracking

## Return Value Patterns

### Success Response Structure
```python
return {
    "success": True,
    "message": "Operation completed successfully",
    "host_id": host_id,
    "container_id": container_id,  # Resource identifier
    "data": processed_data,         # Operation-specific data
    "timestamp": datetime.now().isoformat(),
    # Additional operation-specific fields
}
```

### Error Response Structure
```python
return {
    "success": False,
    "error": str(e),
    "host_id": host_id,
    "container_id": container_id,  # Resource identifier for context
    "timestamp": datetime.now().isoformat(),
}
```

### Consistent Fields Across All Responses
- `success: bool` - Always present for programmatic handling
- `timestamp: str` - ISO format timestamp for all operations
- `host_id: str` - Host context for all operations
- Resource identifiers (`container_id`, `stack_name`, etc.)

## Docker Context Integration

### Command Execution Pattern
```python
async def some_operation(self, host_id: str, resource_id: str):
    try:
        # Build Docker command
        cmd = f"some-command {resource_id}"
        
        # Execute via context manager
        result = await self.context_manager.execute_docker_command(host_id, cmd)
        
        # Process result
        return self._build_success_response(result)
        
    except (DockerCommandError, DockerContextError) as e:
        return self._build_error_response(str(e))
```

### JSON Command Handling
```python
# Commands that return JSON (inspect, version, info) are automatically parsed
json_commands = ["inspect", "version", "info"]
if command_parts and command_parts[0] in json_commands:
    # Returns parsed JSON object directly
    return json_data
else:
    # Returns wrapped output
    return {"output": result.stdout.strip()}
```

## Hybrid Docker Context/SSH Pattern

### Docker Context for Container Operations
```python
# Most container operations use Docker context
async def start_container(self, host_id: str, container_id: str):
    cmd = f"start {container_id}"
    await self.context_manager.execute_docker_command(host_id, cmd)
```

### SSH for Stack Operations (File Access Required)
```python
# Stack operations need SSH for filesystem access
async def _execute_compose_with_file(self, context_name: str, project_name: str, compose_file_path: str):
    # Build SSH command for remote compose file access
    ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
    remote_cmd = f"cd {project_directory} && docker compose -f {compose_file_path} up -d"
    
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: subprocess.run(ssh_cmd + [remote_cmd], ...)  # nosec B603
    )
```

### Why SSH for Stacks?
- Docker contexts cannot access remote filesystem paths
- Compose files need to be on remote host for deployment
- SSH provides filesystem access for file operations
- See: https://github.com/docker/compose/issues/9075

## Error Handling Pattern

### Modern Async Exception Handling (Python 3.11+)

```python
import asyncio
from contextlib import AsyncExitStack

# Custom exception hierarchy
class DockerMCPError(Exception):
    """Base exception for Docker MCP operations"""
    
class DockerCommandError(DockerMCPError):
    """Docker command execution failed"""
    
class DockerContextError(DockerMCPError):
    """Docker context operation failed"""

# Modern async patterns with timeout and exception groups
async def complex_docker_operation(self, host_id: str, container_id: str):
    """Modern async operation with proper timeout and error handling."""
    try:
        # Use asyncio.timeout for all operations (Python 3.11+)
        async with asyncio.timeout(30.0):
            result = await self.context_manager.execute_docker_command(host_id, cmd)
            return self._build_success_response(result)
            
    except* (DockerCommandError, DockerContextError) as eg:  # Exception groups
        errors = [str(e) for e in eg.exceptions]
        logger.error("Docker operation failed", 
                    host_id=host_id, 
                    container_id=container_id,
                    errors=errors)
        return self._build_error_response(f"Docker operation failed: {'; '.join(errors)}")
        
    except TimeoutError:
        logger.error("Docker operation timed out", host_id=host_id, timeout=30.0)
        return self._build_error_response("Docker operation timed out after 30 seconds")
        
    except Exception as e:
        logger.error("Unexpected error", 
                    host_id=host_id, 
                    container_id=container_id, 
                    error=str(e))
        return self._build_error_response(f"Unexpected error: {e}")

# Batch operations with TaskGroup (Python 3.11+)
async def batch_container_operations(self, operations: list[ContainerOperation]):
    """Execute multiple container operations concurrently with proper error handling."""
    results: list[OperationResult] = []
    
    try:
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(self._execute_container_operation(op))
                for op in operations
            ]
        
        # All tasks completed successfully
        results = [await task for task in tasks]
        
    except* (DockerCommandError, DockerContextError) as eg:
        # Handle partial failures in batch operations
        for error in eg.exceptions:
            logger.error("Batch operation partial failure", 
                        operation=error.operation_name,
                        error=str(error))
        
        # Return partial results with error information
        return BatchOperationResult(
            success=False,
            partial_results=results,
            errors=[str(e) for e in eg.exceptions],
            completed_count=len(results)
        )
    
    return BatchOperationResult(
        success=True,
        results=results,
        completed_count=len(results)
    )

# Resource management with async context managers
async def managed_container_operation(self, host_id: str, container_id: str):
    """Complex operation with proper resource management."""
    async with AsyncExitStack() as stack:
        # Acquire multiple resources with automatic cleanup
        connection = await stack.enter_async_context(
            self._get_docker_connection(host_id)
        )
        
        # Optional: Acquire container lock for exclusive operations
        container_lock = await stack.enter_async_context(
            self._acquire_container_lock(container_id)
        )
        
        # Use asyncio.timeout at operation level
        async with asyncio.timeout(60.0):
            return await self._perform_complex_operation(
                connection, container_lock, container_id
            )
```

### Error Response Helpers
```python
def _build_error_response(self, error: str, **context) -> dict[str, Any]:
    """Build consistent error response."""
    return {
        "success": False,
        "error": error,
        "timestamp": datetime.now().isoformat(),
        **context  # Additional context like host_id, resource_id
    }
```

## Data Processing Patterns

### JSON Line Processing
```python
# Many Docker commands return JSON lines (one JSON object per line)
for line in result["output"].strip().split("\n"):
    if line.strip():
        try:
            data = json.loads(line)
            processed_items.append(self._process_item(data))
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON line", line=line)
```

### Enhanced Data Collection
```python
# Tools enrich basic Docker data with additional information
async def list_containers(self, host_id: str):
    # Get basic container list
    containers = await self._get_basic_containers(host_id)
    
    # Enhance each container with detailed info
    for container in containers:
        inspect_info = await self._get_container_inspect_info(host_id, container["id"])
        container.update({
            "volumes": inspect_info.get("volumes", []),
            "networks": inspect_info.get("networks", []),
            "compose_project": inspect_info.get("compose_project", ""),
        })
```

## Validation Patterns

### Input Validation
```python
def _validate_stack_name(self, stack_name: str) -> bool:
    """Validate stack name for security and Docker compatibility."""
    import re
    
    # Alphanumeric with hyphens/underscores only
    pattern = r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
    if not re.match(pattern, stack_name):
        return False
        
    # Length limits
    if len(stack_name) > 63:  # Docker limit
        return False
        
    # Reserved names
    reserved = {"docker", "compose", "system", "network", "volume"}
    if stack_name.lower() in reserved:
        return False
        
    return True
```

### Action Validation
```python
def manage_container(self, action: str, ...):
    valid_actions = ["start", "stop", "restart", "pause", "unpause", "remove"]
    if action not in valid_actions:
        return {
            "success": False,
            "error": f"Invalid action '{action}'. Valid actions: {', '.join(valid_actions)}"
        }
```

## Pagination Support

### Pagination Pattern
```python
async def list_containers(self, host_id: str, limit: int = 20, offset: int = 0):
    # Get all containers
    all_containers = await self._get_all_containers(host_id)
    
    # Apply pagination
    total_count = len(all_containers)
    paginated = all_containers[offset:offset + limit]
    
    return {
        "containers": paginated,
        "pagination": {
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "returned": len(paginated),
            "has_next": (offset + limit) < total_count,
            "has_prev": offset > 0,
        }
    }
```

## Pydantic Model Integration

### Model Usage Pattern
```python
from ..models.container import ContainerStats, PortMapping

# Create models from processed data
stats = ContainerStats(
    container_id=container_id,
    host_id=host_id,
    cpu_percentage=self._parse_percentage(raw_data.get("CPUPerc", "0%")),
    memory_usage=self._parse_memory(raw_data.get("MemUsage", "0B / 0B"))[0],
)

# Return serialized model data
return stats.model_dump()
```

### Model Validation Benefits
- Automatic type validation
- Consistent data structure
- IDE support and documentation
- Serialization/deserialization

## Data Parsing Helpers

### Common Parsing Methods
```python
def _parse_percentage(self, perc_str: str) -> float | None:
    """Parse percentage string like '50.5%'."""
    try:
        return float(perc_str.rstrip("%"))
    except (ValueError, AttributeError):
        return None

def _parse_size(self, size_str: str) -> int | None:
    """Parse size string like '1.5GB' to bytes."""
    units = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
    # Implementation for size conversion
```

### Port Parsing
```python
def _parse_ports_summary(self, ports_str: str) -> list[str]:
    """Parse Docker ports string into simplified format."""
    ports = []
    for port_mapping in ports_str.split(", "):
        if "->" in port_mapping:
            host_part, container_part = port_mapping.split("->")
            ports.append(f"{host_part.strip()}→{container_part.strip()}")
    return ports
```

## Logging Pattern

### Structured Operation Logging
```python
# Log operation start
logger.info(
    "Starting container operation",
    host_id=host_id,
    container_id=container_id,
    action=action
)

# Log operation completion
logger.info(
    "Container operation completed",
    host_id=host_id,
    container_id=container_id,
    action=action,
    duration=time.time() - start_time
)

# Log operation failure
logger.error(
    "Container operation failed",
    host_id=host_id,
    container_id=container_id,
    action=action,
    error=str(e)
)
```

## Security Patterns

### Command Validation
```python
def _validate_docker_command(self, command: str) -> None:
    """Validate Docker command for security."""
    allowed_commands = {"ps", "logs", "start", "stop", "restart", "inspect"}
    
    parts = command.strip().split()
    if not parts or parts[0] not in allowed_commands:
        raise ValueError(f"Command not allowed: {parts[0] if parts else 'empty'}")
```

### SSH Security Options
```python
# Standard SSH security options for automation
ssh_cmd.extend([
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null", 
    "-o", "LogLevel=ERROR",
])
```

### Security Comments
```python
# Mark legitimate subprocess calls
result = await subprocess.run(  # nosec B603 - Docker command execution is intentional
    cmd, check=False, capture_output=True, text=True
)
```

## Stream/Real-time Support

### Log Streaming Setup
```python
async def stream_container_logs_setup(self, host_id: str, container_id: str):
    """Setup real-time log streaming endpoint."""
    stream_config = LogStreamRequest(
        host_id=host_id,
        container_id=container_id,
        follow=True,
        tail=100
    )
    
    # Return streaming endpoint information
    return {
        "success": True,
        "stream_id": stream_id,
        "stream_endpoint": f"/streams/logs/{stream_id}",
        "config": stream_config.model_dump(),
    }
```

## Tool Method Categories

### Standard Method Types
- **List Methods**: `list_containers()`, `list_stacks()` - List resources with pagination
- **Info Methods**: `get_container_info()` - Get detailed single resource information  
- **Action Methods**: `start_container()`, `deploy_stack()` - Perform operations
- **Management Methods**: `manage_container()` - Unified lifecycle operations
- **Validation Methods**: `_validate_stack_name()` - Input validation helpers
- **Helper Methods**: `_parse_ports()`, `_build_error_response()` - Data processing

Tools provide the technical implementation layer between services (business logic) and core modules (infrastructure), focusing on Docker command execution, data processing, and response formatting.
