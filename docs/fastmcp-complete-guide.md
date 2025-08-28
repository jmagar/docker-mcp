# FastMCP Complete Guide: Consolidated Tools & Parameter Annotations

## Overview

This comprehensive guide covers two essential FastMCP patterns:

1. **Consolidated Action-Parameter Pattern** - Architectural approach for grouping related operations
2. **Parameter Type Annotations** - Proper annotation techniques to avoid "unknown" parameter types

Together, these patterns create efficient, maintainable, and user-friendly FastMCP servers.

---

# Part I: Consolidated Action-Parameter Pattern

## What is the Consolidated Action-Parameter Pattern?

The Consolidated Action-Parameter Pattern is an architectural approach for organizing FastMCP tools that groups related operations under a single tool with an `action` parameter, rather than creating separate tools for each operation.

## Benefits

### Context Efficiency
- **Token Reduction**: Our 3 consolidated tools (27 actions) use ~5k tokens vs Playwright's 21 individual tools using ~9.7k tokens
- **2.6x more efficient**: 181 tokens per function vs 464 tokens per function
- **Better scaling**: Adding new actions to existing tools is more efficient than creating new tools

### User Experience
- **Cleaner interface**: 3 logical groups instead of 27 separate tools
- **Easier discovery**: Related operations are grouped together
- **Consistent parameters**: Shared parameters like `host_id` defined once per group
- **Logical organization**: Operations naturally group by domain (hosts, containers, stacks)

### Maintainability
- **Reduced duplication**: Common validation and error handling
- **Centralized routing**: Single dispatch point per domain
- **Consistent patterns**: Uniform approach across all operations

## Implementation Pattern

### 1. Basic Structure

```python
from typing import Annotated, Literal
from pydantic import Field
from fastmcp import FastMCP

mcp = FastMCP(name="ConsolidatedServer")

@mcp.tool
async def domain_manager(
    # Action parameter - defines what operation to perform
    action: Annotated[Literal["list", "create", "update", "delete"], Field(description="Action to perform")],
    
    # Required parameters - used by multiple actions
    resource_id: Annotated[str, Field(description="Resource identifier", min_length=1)],
    
    # Optional parameters - provide defaults for all actions
    name: Annotated[str, Field(default="", description="Resource name")] = "",
    description: Annotated[str, Field(default="", description="Resource description")] = "",
    tags: Annotated[list[str], Field(default_factory=list, description="Resource tags")] = [],
    
    # Action-specific parameters - only used by certain actions
    force: Annotated[bool, Field(default=False, description="Force the operation")] = False,
    dry_run: Annotated[bool, Field(default=False, description="Perform a dry run")] = False
) -> dict[str, Any]:
    """Consolidated resource management tool.
    
    Actions:
    - list: List all resources
    - create: Create a new resource (requires: name)
    - update: Update an existing resource (requires: resource_id)
    - delete: Delete a resource (requires: resource_id; optional: force)
    """
    
    # Validate action
    valid_actions = ["list", "create", "update", "delete"]
    if action not in valid_actions:
        return {
            "success": False,
            "error": f"Invalid action '{action}'. Must be one of: {', '.join(valid_actions)}"
        }
    
    # Route to appropriate handler
    try:
        if action == "list":
            return await handle_list_resources()
        elif action == "create":
            if not name:
                return {"success": False, "error": "name is required for create action"}
            return await handle_create_resource(name, description, tags)
        elif action == "update":
            if not resource_id:
                return {"success": False, "error": "resource_id is required for update action"}
            return await handle_update_resource(resource_id, name, description, tags)
        elif action == "delete":
            if not resource_id:
                return {"success": False, "error": "resource_id is required for delete action"}
            return await handle_delete_resource(resource_id, force, dry_run)
    except Exception as e:
        return {
            "success": False,
            "error": f"Action '{action}' failed: {str(e)}"
        }
```

### 2. Action Parameter Design

#### Use Literal Types for Actions

```python
# ✅ Good - Explicit list of valid actions
action: Annotated[Literal["start", "stop", "restart", "status"], Field(description="Container action")]

# ❌ Avoid - Too generic
action: Annotated[str, Field(description="Action to perform")]
```

#### Group Related Operations

```python
# ✅ Good groupings by domain
docker_hosts_actions = ["list", "add", "remove", "ports", "cleanup"]
docker_container_actions = ["list", "info", "start", "stop", "restart", "logs"]  
docker_compose_actions = ["list", "deploy", "up", "down", "build", "logs", "migrate"]

# ❌ Avoid mixing unrelated domains
mixed_actions = ["list_hosts", "start_container", "deploy_stack"]  # Too broad
```

---

# Part II: Parameter Type Annotations

## The Problem

When using FastMCP, parameters can show up as "unknown" type in MCP clients, making it difficult for users and LLMs to understand what types of values are expected. This issue is caused by incorrect type annotation patterns that confuse FastMCP's Pydantic-based type introspection system.

## The Solution: Working Patterns

### ✅ Required Parameters

For required parameters, use this pattern:

```python
from typing import Annotated
from pydantic import Field

@mcp.tool
def example_tool(
    # Required string with validation
    host_id: Annotated[str, Field(description="Host identifier", min_length=1)],
    
    # Required integer with constraints
    port: Annotated[int, Field(description="Port number", ge=1, le=65535)],
    
    # Required literal (enum-like)
    action: Annotated[Literal["start", "stop", "restart"], Field(description="Action to perform")],
    
    # Required boolean
    enabled: Annotated[bool, Field(description="Whether feature is enabled")]
) -> dict:
    pass
```

### ✅ Optional Parameters

For optional parameters, **always include `default=` in the Field AND provide a default value**:

```python
@mcp.tool
def example_tool(
    # Optional string
    ssh_key_path: Annotated[str, Field(default="", description="Path to SSH private key file")] = "",
    
    # Optional integer with constraints
    timeout: Annotated[int, Field(default=30, ge=1, le=300, description="Timeout in seconds")] = 30,
    
    # Optional boolean
    test_connection: Annotated[bool, Field(default=True, description="Test connection")] = True,
    
    # Optional list with default_factory
    tags: Annotated[list[str], Field(default_factory=list, description="List of tags")] = [],
    
    # Optional dict with default_factory
    metadata: Annotated[dict[str, str], Field(default_factory=dict, description="Metadata")] = {},
    
    # Optional enum-like parameter (use str, not Literal for optional)
    log_level: Annotated[str, Field(default="info", description="Log level")] = "info"
) -> dict:
    pass
```

### Key Rules for Optional Parameters

1. **Use `default=` in Field** - Always specify the default value in the Field definition
2. **Provide default value after `=`** - Also provide the default value after the parameter annotation
3. **No `| None` unions** - Avoid union types entirely for FastMCP compatibility
4. **Use `default_factory=` for collections** - For lists/dicts, use `default_factory=list` or `default_factory=dict`
5. **Use `str` instead of `Literal` for optional enums** - Literal types in optional parameters cause issues

## ❌ Patterns That Don't Work

### Union Types Inside Annotated

```python
# ❌ WRONG - Union inside Annotated
ssh_key_path: Annotated[str | None, Field(description="Path to SSH private key")]

# ❌ WRONG - Optional with union inside Annotated
tags: Annotated[list[str] | None, Field(description="Host tags")]
```

### Union Types Outside Annotated

```python
# ❌ WRONG - Union outside Annotated
ssh_key_path: Annotated[str, Field(description="Path to SSH private key")] | None

# ❌ WRONG - This also doesn't work
compose_path: Annotated[str, Field(description="Docker Compose file path")] | None = None
```

### Missing Default in Field

```python
# ❌ WRONG - Missing default= in Field
ssh_key_path: Annotated[str, Field(description="Path to SSH private key")] = ""

# ❌ WRONG - FastMCP needs explicit default in Field
timeout: Annotated[int, Field(ge=1, le=300, description="Timeout")] = 30
```

### Simple Union Types Without Field

```python
# ❌ WRONG - Simple union types show as "unknown"
ssh_key_path: str | None = None
tags: list[str] | None = None
```

---

# Part III: Complete Implementation Example

## Real-World Example: Docker MCP Server

### Tool Organization

```python
# Before: 27 separate tools
@mcp.tool
async def list_docker_hosts(): pass

@mcp.tool  
async def add_docker_host(): pass

@mcp.tool
async def list_host_ports(): pass

# ... 24 more tools

# After: 3 consolidated tools
@mcp.tool
async def docker_hosts(action: Literal["list", "add", "ports", ...], ...): pass

@mcp.tool
async def docker_container(action: Literal["list", "info", "start", ...], ...): pass

@mcp.tool
async def docker_compose(action: Literal["list", "deploy", "up", ...], ...): pass
```

### Complete Implementation with Proper Annotations

```python
from typing import Annotated, Literal
from pydantic import Field
from fastmcp import FastMCP

mcp = FastMCP(name="DockerMCPServer")

@mcp.tool
async def docker_hosts(
    # Action parameter with Literal type
    action: Annotated[Literal[
        "list", "add", "ports", "compose_path", "import_ssh", 
        "cleanup", "schedule", "reserve_port", "release_port"
    ], Field(description="Action to perform")],
    
    # Core parameters with proper defaults
    host_id: Annotated[str, Field(default="", description="Host identifier", min_length=1)] = "",
    ssh_host: Annotated[str, Field(default="", description="SSH hostname or IP address", min_length=1)] = "",
    ssh_user: Annotated[str, Field(default="", description="SSH username", min_length=1)] = "",
    
    # Optional parameters with explicit defaults in Field
    ssh_port: Annotated[int, Field(default=22, ge=1, le=65535, description="SSH port number")] = 22,
    ssh_key_path: Annotated[str, Field(default="", description="Path to SSH private key file")] = "",
    description: Annotated[str, Field(default="", description="Host description")] = "",
    tags: Annotated[list[str], Field(default_factory=list, description="Host tags")] = [],
    test_connection: Annotated[bool, Field(default=True, description="Test connection when adding host")] = True,
    
    # Action-specific parameters
    port: Annotated[int, Field(default=0, ge=1, le=65535, description="Port number for reservation operations")] = 0,
    cleanup_type: Annotated[str, Field(default="", description="Type of cleanup to perform")] = "",
    export_format: Annotated[str, Field(default="", description="Export format for port data")] = "",
    
    # Advanced validation examples
    schedule_time: Annotated[str, Field(default="", pattern=r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$", description="Time to run cleanup in HH:MM format (24-hour)")] = ""
) -> dict[str, Any]:
    """Consolidated Docker hosts management tool.

    Actions:
    • list: List all configured Docker hosts
      - Required: none
      
    • add: Add a new Docker host
      - Required: host_id, ssh_host, ssh_user
      - Optional: ssh_port, ssh_key_path, description, tags, compose_path, enabled, test_connection
      
    • ports: List port mappings for a host
      - Required: host_id
      - Optional: include_stopped, export_format, filter_project, filter_range, filter_protocol, scan_available, suggest_next, use_cache
      
    • compose_path: Update host compose path
      - Required: host_id, compose_path
      
    • import_ssh: Import hosts from SSH config
      - Required: none
      - Optional: ssh_config_path, selected_hosts, compose_path_overrides, auto_confirm
      
    • reserve_port: Reserve a port on a host
      - Required: host_id, port, service_name
      - Optional: protocol, reserved_by, expires_days, notes
      
    • release_port: Release a port reservation
      - Required: host_id, port
      - Optional: protocol
    """
    
    # Validation
    valid_actions = ["list", "add", "ports", "compose_path", "import_ssh", "cleanup", "schedule", "reserve_port", "release_port"]
    if action not in valid_actions:
        return {
            "success": False,
            "error": f"Invalid action '{action}'. Must be one of: {', '.join(valid_actions)}"
        }
    
    try:
        # Route to appropriate handler
        if action == "list":
            return await self.list_docker_hosts()
        elif action == "add":
            # Validate required parameters for this action
            if not all([host_id, ssh_host, ssh_user]):
                return {
                    "success": False, 
                    "error": "host_id, ssh_host, and ssh_user are required for add action"
                }
            return await self.add_docker_host(host_id, ssh_host, ssh_user, ssh_port, ssh_key_path, description, tags)
        elif action == "ports":
            if not host_id:
                return {"success": False, "error": "host_id is required for ports action"}
            return await self.list_host_ports(host_id, export_format)
        elif action == "reserve_port":
            if not all([host_id, port]):
                return {"success": False, "error": "host_id and port are required for reserve_port action"}
            return await self.reserve_port(host_id, port)
        # ... handle other actions
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Action '{action}' failed: {str(e)}",
            "action": action
        }
```

## Parameter Strategies

### Required vs Optional Parameters

```python
@mcp.tool
async def resource_manager(
    # Required for all actions - no default
    action: Annotated[Literal["list", "create", "update"], Field(description="Action to perform")],
    
    # Conditionally required - validate in function body
    resource_id: Annotated[str, Field(default="", description="Resource identifier")] = "",
    
    # Always optional with sensible defaults
    timeout: Annotated[int, Field(default=30, ge=1, le=300, description="Operation timeout")] = 30,
    force: Annotated[bool, Field(default=False, description="Force the operation")] = False
) -> dict:
    # Validate conditionally required parameters
    if action in ["update", "delete"] and not resource_id:
        return {"success": False, "error": f"resource_id is required for {action} action"}
```

### Parameter Grouping by Usage

```python
@mcp.tool
async def container_manager(
    action: Annotated[Literal["list", "start", "stop", "logs"], Field(description="Action to perform")],
    
    # Core identification parameters
    host_id: Annotated[str, Field(default="", description="Docker host identifier")] = "",
    container_id: Annotated[str, Field(default="", description="Container identifier")] = "",
    
    # List-specific parameters
    all_containers: Annotated[bool, Field(default=False, description="Include stopped containers")] = False,
    limit: Annotated[int, Field(default=20, ge=1, le=1000, description="Max results")] = 20,
    
    # Logs-specific parameters  
    follow: Annotated[bool, Field(default=False, description="Follow log output")] = False,
    lines: Annotated[int, Field(default=100, ge=1, le=10000, description="Number of log lines")] = 100,
    
    # Operation parameters
    force: Annotated[bool, Field(default=False, description="Force the operation")] = False,
    timeout: Annotated[int, Field(default=10, ge=1, le=300, description="Timeout in seconds")] = 10
):
    pass
```

---

# Part IV: Validation and Error Handling

## Action Validation Pattern

```python
def validate_action(action: str, valid_actions: list[str]) -> tuple[bool, str]:
    """Validate action parameter."""
    if action not in valid_actions:
        return False, f"Invalid action '{action}'. Must be one of: {', '.join(valid_actions)}"
    return True, ""

# Usage in tool
valid_actions = ["list", "create", "update", "delete"]
is_valid, error_msg = validate_action(action, valid_actions)
if not is_valid:
    return {"success": False, "error": error_msg}
```

## Parameter Validation by Action

```python
def validate_parameters_for_action(action: str, **params) -> tuple[bool, str]:
    """Validate required parameters for specific actions."""
    
    if action == "create":
        if not params.get("name"):
            return False, "name is required for create action"
    
    elif action in ["update", "delete"]:
        if not params.get("resource_id"):
            return False, f"resource_id is required for {action} action"
    
    elif action == "deploy":
        if not params.get("compose_content"):
            return False, "compose_content is required for deploy action"
    
    # Add range validations
    if params.get("port") and not (1 <= params["port"] <= 65535):
        return False, "port must be between 1 and 65535"
        
    return True, ""
```

## Validation Constraints

FastMCP supports all Pydantic validation constraints:

```python
# Numeric constraints
count: Annotated[int, Field(default=0, ge=0, le=100, description="Count (0-100)")] = 0,
ratio: Annotated[float, Field(default=1.0, gt=0, lt=2.0, description="Ratio (0-2)")] = 1.0,
multiple: Annotated[int, Field(default=10, multiple_of=5, description="Must be multiple of 5")] = 10,

# String constraints
name: Annotated[str, Field(default="", min_length=1, max_length=50, description="Name (1-50 chars)")] = "",
pattern_field: Annotated[str, Field(default="", pattern=r"^[A-Z]{2}\d{4}$", description="Pattern: XX0000")] = "",

# Collection constraints
items: Annotated[list[str], Field(default_factory=list, min_length=0, max_length=10, description="0-10 items")] = [],
```

---

# Part V: Routing and Dispatch

## Service Layer Pattern

```python
class ResourceService:
    async def list_resources(self) -> dict:
        """List all resources."""
        pass
    
    async def create_resource(self, name: str, **kwargs) -> dict:
        """Create a new resource."""
        pass
    
    async def update_resource(self, resource_id: str, **kwargs) -> dict:
        """Update existing resource."""
        pass

@mcp.tool 
async def resource_manager(action: str, **params) -> dict:
    service = ResourceService()
    
    # Route to appropriate service method
    if action == "list":
        return await service.list_resources()
    elif action == "create":
        return await service.create_resource(**params)
    elif action == "update":
        return await service.update_resource(**params)
    # ... etc
```

## Direct Dispatch Pattern

```python
@mcp.tool
async def container_manager(action: str, host_id: str, container_id: str, **kwargs) -> dict:
    """Direct dispatch to handler functions."""
    
    # Route to handler functions
    handlers = {
        "list": handle_list_containers,
        "info": handle_container_info, 
        "start": handle_start_container,
        "stop": handle_stop_container,
        "logs": handle_container_logs
    }
    
    handler = handlers.get(action)
    if not handler:
        return {"success": False, "error": f"Unknown action: {action}"}
    
    try:
        return await handler(host_id, container_id, **kwargs)
    except Exception as e:
        return {"success": False, "error": f"Action '{action}' failed: {str(e)}"}

async def handle_list_containers(host_id: str, **kwargs) -> dict:
    """Handle container listing."""
    all_containers = kwargs.get("all_containers", False)
    limit = kwargs.get("limit", 20)
    # Implementation...
    return {"success": True, "containers": []}
```

## Error Handling Pattern

```python
async def consolidated_tool(action: str, **params) -> dict:
    # Always return consistent error format
    def error_response(message: str) -> dict:
        return {
            "success": False,
            "error": message,
            "action": action  # Include action for debugging
        }
    
    # Validate action
    if action not in VALID_ACTIONS:
        return error_response(f"Invalid action '{action}'")
    
    # Validate parameters
    validation_result = validate_params_for_action(action, **params)
    if not validation_result.success:
        return error_response(validation_result.error)
    
    try:
        # Execute action
        result = await execute_action(action, **params)
        return {
            "success": True,
            "action": action,
            **result
        }
    except Exception as e:
        logger.error(f"Action '{action}' failed", exc_info=True)
        return error_response(f"Execution failed: {str(e)}")
```

---

# Part VI: Best Practices

## 1. Action Naming

```python
# ✅ Good - Verb-based, clear actions
["list", "create", "update", "delete", "start", "stop", "restart"]

# ✅ Good - Domain-specific actions
["deploy", "scale", "migrate", "backup", "restore"]

# ❌ Avoid - Ambiguous or too generic
["manage", "handle", "process", "execute"]

# ❌ Avoid - Mixing abstraction levels  
["list", "get_detailed_info", "quick_start"]
```

## 2. Parameter Organization

```python
@mcp.tool
async def consolidated_tool(
    # 1. Action parameter (always first)
    action: Annotated[Literal[...], Field(description="Action to perform")],
    
    # 2. Core required parameters (used by most actions)
    resource_id: Annotated[str, Field(description="Resource identifier")],
    
    # 3. Core optional parameters (used by most actions)
    name: Annotated[str, Field(default="", description="Resource name")] = "",
    
    # 4. Action-specific parameters (grouped by usage)
    # List parameters
    limit: Annotated[int, Field(default=20, description="Max results")] = 20,
    # Deploy parameters  
    compose_content: Annotated[str, Field(default="", description="Compose file")] = "",
    # Operation parameters
    force: Annotated[bool, Field(default=False, description="Force operation")] = False
):
    pass
```

## 3. Documentation Patterns

```python
@mcp.tool
async def domain_manager(action, ...):
    """Consolidated domain management tool.

    Actions:
    • list: List all resources
      - Required: none
      
    • create: Create a new resource
      - Required: name
      - Optional: description, tags
      
    • update: Update resource
      - Required: resource_id
      - Optional: name, description
      
    • delete: Delete resource
      - Required: resource_id
      - Optional: force
      
    • deploy: Deploy resource
      - Required: resource_id, config
      - Optional: dry_run
    """
```

---

# Part VII: Migration Guide

## Converting Individual Tools to Consolidated

### Step 1: Identify Tool Groups
```python
# Group related tools by domain
container_tools = ["list_containers", "start_container", "stop_container", "get_container_logs"]
host_tools = ["list_hosts", "add_host", "remove_host", "test_host_connection"]
```

### Step 2: Design Action Parameter
```python
# Extract action from tool names
container_actions = ["list", "start", "stop", "logs"]  # Remove "container" prefix
host_actions = ["list", "add", "remove", "test"]      # Remove "host" prefix
```

### Step 3: Consolidate Parameters
```python
# Before - separate tools with duplicate parameters
async def start_container(host_id: str, container_id: str, timeout: int = 30): pass
async def stop_container(host_id: str, container_id: str, timeout: int = 30, force: bool = False): pass

# After - consolidated with shared parameters and proper annotations
async def container_manager(
    action: Annotated[Literal["start", "stop"], Field(description="Container action")],
    host_id: Annotated[str, Field(description="Docker host identifier")],
    container_id: Annotated[str, Field(description="Container identifier")],
    timeout: Annotated[int, Field(default=30, ge=1, le=300, description="Operation timeout")] = 30,
    force: Annotated[bool, Field(default=False, description="Force the operation")] = False  # Used by stop, ignored by start
): pass
```

### Step 4: Implement Routing
```python
async def container_manager(action: str, **params):
    # Map actions to original functions
    action_map = {
        "list": original_list_containers,
        "start": original_start_container,
        "stop": original_stop_container,
        "logs": original_get_container_logs
    }
    
    handler = action_map.get(action)
    return await handler(**params)
```

---

# Part VIII: Troubleshooting

## Troubleshooting "Unknown" Parameter Types

### Step 1: Check the Pattern

Ensure your parameters follow the correct pattern:

```python
# ✅ CORRECT
parameter: Annotated[Type, Field(default=default_value, description="...")] = default_value
```

### Step 2: Avoid Union Types

If a parameter shows as "unknown", check if it uses union types:

```python
# ❌ Remove this
parameter: str | None = None

# ✅ Replace with this  
parameter: Annotated[str, Field(default="", description="...")] = ""
```

### Step 3: Add Explicit Defaults

Ensure both `default=` in Field and `= value` are present:

```python
# ❌ Missing default in Field
parameter: Annotated[str, Field(description="...")] = ""

# ✅ Add explicit default
parameter: Annotated[str, Field(default="", description="...")] = ""
```

### Step 4: Use default_factory for Collections

For lists and dicts, use `default_factory`:

```python
# ✅ Lists
tags: Annotated[list[str], Field(default_factory=list, description="Tags")] = []

# ✅ Dicts  
metadata: Annotated[dict[str, str], Field(default_factory=dict, description="Metadata")] = {}
```

### Step 5: Test MCP Introspection

After making changes:

1. Restart your FastMCP server
2. Reconnect your MCP client
3. Check that parameters show proper types (string, integer, boolean, array, object)
4. If any still show "unknown", verify they follow the patterns above

## Common Issues

1. **Too Many Parameters**: If you have >15 parameters, consider splitting into multiple consolidated tools
2. **Parameter Conflicts**: When actions need mutually exclusive parameters, use validation in the function body
3. **Action Overlap**: Avoid having similar actions across different consolidated tools
4. **Validation Complexity**: If validation logic becomes too complex, consider separate validation functions

## Performance Considerations

- **Token Efficiency**: Consolidated tools use significantly fewer tokens than individual tools
- **Context Overhead**: Each tool adds ~400-500 tokens to context - consolidation reduces this multiplicatively
- **Parameter Parsing**: More parameters per tool, but FastMCP handles this efficiently
- **Route Dispatch**: Minimal overhead compared to context token savings

---

# Summary

## Key Takeaways

### For Consolidated Action-Parameter Pattern:
- **Massive token efficiency**: 2.6x more efficient than individual tools
- **Better organization**: Logical grouping of related operations  
- **Cleaner interfaces**: Fewer tools in MCP client listings
- **Easier maintenance**: Centralized validation and error handling
- **Consistent UX**: Uniform parameter patterns across actions

### For Parameter Type Annotations:
1. **Required**: `Annotated[Type, Field(description="...")]`
2. **Optional**: `Annotated[Type, Field(default=value, description="...")] = value`
3. **Collections**: Use `default_factory=list` or `default_factory=dict`
4. **No Unions**: Avoid `| None` entirely
5. **Explicit Defaults**: Always specify `default=` in Field for optional parameters
6. **Use Enums**: For action parameters, use proper Enum classes instead of Literal types for better type safety

### For Documentation Format:
- **Use bullet points**: • for main actions, - for sub-bullets
- **Clear parameter organization**: Separate Required and Optional sections
- **Consistent formatting**: Same format across all consolidated tools

Together, these patterns create FastMCP servers that are:
- **Token-efficient** (critical for context limits)
- **User-friendly** (clean, discoverable interfaces)
- **Type-safe** (proper introspection and validation)
- **Maintainable** (consistent patterns and centralized logic)
- **Scalable** (easy to add new actions and parameters)

This approach is especially valuable for domain-rich APIs where you have many related operations that share common parameters and validation logic.