# Models Layer - Development Memory

## Pydantic Model Architecture

### Base Model Pattern
```python
from pydantic import BaseModel, Field

class ContainerInfo(BaseModel):
    """Information about a Docker container."""
    
    # Required fields
    container_id: str
    name: str
    host_id: str  # Always include host context
    
    # Optional fields with defaults
    status: str = "unknown"
    ports: list[dict[str, Any]] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
```

### Model Dependencies
- **Pydantic**: `BaseModel` for data validation and serialization
- **Field**: For advanced field configuration and validation
- **Type Hints**: Modern Python 3.10+ union syntax (`str | None`)

## Model Categories

### Information Models (Core Entity Data)
```python
class ContainerInfo(BaseModel):
    """Static information about a Docker container."""
    container_id: str
    name: str
    image: str
    status: str
    host_id: str

class HostInfo(BaseModel):
    """Configuration information about a Docker host."""
    host_id: str
    hostname: str
    user: str
    port: int = 22
```

### Status Models (Real-time State)
```python
class HostStatus(BaseModel):
    """Current status of a Docker host."""
    host_id: str
    online: bool
    ssh_connected: bool
    docker_connected: bool
    error_message: str | None = None
    last_check: str  # ISO timestamp
    response_time_ms: float | None = None
```

### Statistics Models (Performance Metrics)
```python
class ContainerStats(BaseModel):
    """Resource statistics for a container."""
    container_id: str
    host_id: str
    cpu_percentage: float | None = None
    memory_usage: int | None = None  # bytes
    memory_limit: int | None = None  # bytes
    network_rx: int | None = None    # bytes
    network_tx: int | None = None    # bytes
```

### Request Models (API Input)
```python
class DeployStackRequest(BaseModel):
    """Request to deploy a Docker Compose stack."""
    host_id: str
    stack_name: str
    compose_content: str
    environment: dict[str, str] = Field(default_factory=dict)
    pull_images: bool = True
    recreate: bool = False
```

### Response Models (Complex Output)
```python
class PortListResponse(BaseModel):
    """Complete port listing response with analysis."""
    host_id: str
    total_ports: int
    total_containers: int
    port_mappings: list[PortMapping]
    conflicts: list[PortConflict] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    timestamp: str
```

## Field Patterns

### Required vs Optional Fields
```python
# Required fields (no default value)
container_id: str
host_id: str
name: str

# Optional fields with None default
description: str | None = None
error_message: str | None = None
docker_version: str | None = None

# Optional fields with value defaults
port: int = 22
enabled: bool = True
pull_images: bool = True
```

### Mutable Default Handling
```python
# CORRECT: Use Field(default_factory=...) for mutable defaults
ports: list[dict[str, Any]] = Field(default_factory=list)
labels: dict[str, str] = Field(default_factory=dict)
tags: list[str] = Field(default_factory=list)
environment: dict[str, str] = Field(default_factory=dict)

# INCORRECT: Never use mutable defaults directly
# ports: list[dict[str, Any]] = []  # This causes shared state bugs!
```

### Type Annotations
```python
# Modern Python 3.10+ union syntax (PREFERRED)
description: str | None = None
memory_usage: int | None = None
load_average: list[float] | None = None

# Legacy Union syntax (avoid if possible)
from typing import Union, Optional
description: Optional[str] = None
memory_usage: Union[int, None] = None
```

### Unit Documentation
```python
# Always document units in comments for numeric fields
memory_usage: int | None = None     # bytes
memory_limit: int | None = None     # bytes
response_time_ms: float | None = None  # milliseconds
disk_total: int | None = None       # bytes
port: int = 22                      # TCP port number
```

## Common Field Patterns

### Resource Identifiers
```python
# Always include host context for multi-host operations
host_id: str              # Host identifier (required)
container_id: str         # Container identifier
stack_name: str          # Stack identifier
```

### Timestamp Fields
```python
# Use ISO format strings for timestamps
created: str | None = None        # ISO 8601 format
updated: str | None = None        # ISO 8601 format
timestamp: str                    # Current operation timestamp
last_check: str                   # ISO 8601 format
last_ping: str | None = None      # ISO 8601 format
```

### Status Fields
```python
# Boolean status flags
online: bool
enabled: bool = True
connected: bool = False
ssh_connected: bool
docker_connected: bool

# String status descriptions
status: str                       # Human-readable status
state: str                        # Machine state
error_message: str | None = None  # Error details when failed
```

### Collection Fields
```python
# Always use default_factory for collections
services: list[str] = Field(default_factory=list)
tags: list[str] = Field(default_factory=list)
labels: dict[str, str] = Field(default_factory=dict)
environment: dict[str, str] = Field(default_factory=dict)
ports: list[dict[str, Any]] = Field(default_factory=list)
```

## Naming Conventions

### Model Names
```python
# Pattern: {Resource}{Purpose}
ContainerInfo        # Entity information
HostStatus          # Current status
ContainerStats      # Performance metrics
DeployStackRequest  # API request
PortListResponse    # API response
ContainerAction     # Operation request
LogStreamRequest    # Configuration
```

### Field Names
```python
# Use snake_case consistently
container_id         # Not containerId
host_id             # Not hostId
memory_usage        # Not memoryUsage
response_time_ms    # Not responseTimeMs
ssh_connected       # Not sshConnected
```

## Validation Patterns

### Automatic Validation
```python
# Pydantic automatically validates:
port: int = 22              # Must be integer
enabled: bool = True        # Must be boolean  
tags: list[str]            # Must be list of strings
memory_usage: int | None   # Must be int or None
```

### Custom Field Validation
```python
from pydantic import Field, validator

class HostInfo(BaseModel):
    port: int = Field(ge=1, le=65535, description="Valid TCP port")
    hostname: str = Field(min_length=1, description="Non-empty hostname")
    
    @validator('port')
    def validate_port(cls, v):
        if not (1 <= v <= 65535):
            raise ValueError('Port must be between 1 and 65535')
        return v
```

## Model Usage Patterns

### Creation and Serialization
```python
# Create model instance
stats = ContainerStats(
    container_id="abc123",
    host_id="production-1",
    cpu_percentage=45.2,
    memory_usage=512 * 1024 * 1024  # 512MB in bytes
)

# Serialize to dictionary
return stats.model_dump()

# Serialize to JSON string  
json_data = stats.model_dump_json()
```

### Partial Data Handling
```python
# Models handle partial data gracefully with optional fields
partial_stats = ContainerStats(
    container_id="abc123",
    host_id="production-1"
    # cpu_percentage, memory_usage, etc. will be None
)
```

### Model Composition
```python
# Models can contain other models
class PortListResponse(BaseModel):
    host_id: str
    port_mappings: list[PortMapping]      # List of other models
    conflicts: list[PortConflict]         # List of other models
    
class PortMapping(BaseModel):
    # Individual port mapping details
    host_port: str
    container_port: str
    # ...
```

## Error Handling

### Validation Errors
```python
from pydantic import ValidationError

try:
    container = ContainerInfo(
        container_id="",  # Invalid empty string
        name="test",
        host_id="prod-1"
    )
except ValidationError as e:
    # Handle validation errors
    logger.error("Model validation failed", errors=e.errors())
```

### Flexible Field Access
```python
# Models provide safe field access
stats = ContainerStats(container_id="abc", host_id="prod")

# Safe access - returns None for optional fields
cpu_usage = stats.cpu_percentage  # Returns None if not set

# Dictionary access for dynamic fields
stats_dict = stats.model_dump()
memory = stats_dict.get("memory_usage", 0)  # Default to 0 if None
```

## Integration Patterns

### With Tools Layer
```python
# Tools create and return model data
async def get_container_stats(self, host_id: str, container_id: str):
    raw_stats = await self._get_raw_stats(host_id, container_id)
    
    # Create validated model
    stats = ContainerStats(
        container_id=container_id,
        host_id=host_id,
        cpu_percentage=self._parse_percentage(raw_stats.get("cpu")),
        memory_usage=self._parse_memory(raw_stats.get("memory"))
    )
    
    return stats.model_dump()
```

### With Services Layer
```python
# Services receive model data from tools
async def format_container_stats(self, stats_data: dict):
    # Validate and create model
    stats = ContainerStats(**stats_data)
    
    # Format for user display
    return f"CPU: {stats.cpu_percentage}%, Memory: {self._format_bytes(stats.memory_usage)}"
```

## Model Testing

### Test Data Creation
```python
def test_container_stats():
    stats = ContainerStats(
        container_id="test-123",
        host_id="test-host",
        cpu_percentage=25.5,
        memory_usage=1024 * 1024 * 512  # 512MB
    )
    
    assert stats.cpu_percentage == 25.5
    assert stats.memory_usage == 536870912
    assert stats.host_id == "test-host"
```

Models provide type-safe data structures with automatic validation, ensuring data consistency and reducing runtime errors across the entire application stack.
