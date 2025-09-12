# Models Layer - Development Memory

## Pydantic v2 Model Architecture (2.0+)

### Modern Base Model Pattern
```python
from pydantic import BaseModel, Field, ConfigDict
from typing import Annotated, Literal, Any
from datetime import datetime
import uuid

class ContainerInfo(BaseModel):
    """Information about a Docker container with Pydantic v2 patterns."""
    
    # Modern model configuration (Pydantic v2)
    model_config = ConfigDict(
        # Performance and validation settings
        validate_assignment=True,        # Validate on assignment
        use_enum_values=True,           # Use enum values in serialization
        arbitrary_types_allowed=True,    # Allow custom types
        str_strip_whitespace=True,      # Auto-strip strings
        populate_by_name=True,          # Support field aliases
        
        # JSON schema generation
        json_schema_extra={
            "examples": [{
                "container_id": "abc123def456",
                "name": "web-server",
                "host_id": "production-1",
                "status": "running",
                "ports": [{"host": "80", "container": "80"}]
            }]
        }
    )
    
    # Required fields with enhanced validation
    container_id: Annotated[str, Field(
        min_length=8, 
        max_length=64,
        pattern=r"^[a-f0-9]{8,64}$",
        description="Docker container ID (hex)"
    )]
    
    name: Annotated[str, Field(
        min_length=1,
        max_length=128, 
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
        description="Container name (Docker naming rules)"
    )]
    
    host_id: Annotated[str, Field(
        min_length=1,
        max_length=64,
        description="Host identifier where container runs"
    )]
    
    # Status with literal types for type safety
    status: Literal["created", "restarting", "running", "removing", "paused", "exited", "dead"] = "unknown"
    
    # Collections with proper defaults
    ports: Annotated[list[dict[str, Any]], Field(
        default_factory=list,
        description="Port mappings between host and container"
    )]
    
    labels: Annotated[dict[str, str], Field(
        default_factory=dict,
        description="Docker labels as key-value pairs"
    )]
    
    # Timestamps with proper types
    created_at: datetime | None = Field(
        default=None,
        description="When the container was created"
    )
    
    # Computed fields (Pydantic v2 feature)
    @property
    def is_running(self) -> bool:
        """Computed property indicating if container is running."""
        return self.status == "running"
    
    # Model validators (Pydantic v2 syntax)
    def model_post_init(self, __context: Any) -> None:
        """Post-init validation and processing."""
        # Normalize container name if needed
        if self.name and not self.name.startswith('/'):
            self.name = self.name.lstrip('/')

# Advanced field validation patterns
class StackDeployRequest(BaseModel):
    """Modern stack deployment request with comprehensive validation."""
    
    model_config = ConfigDict(
        validate_assignment=True,
        str_strip_whitespace=True,
        # Custom field aliases for API compatibility
        alias_generator=lambda field_name: field_name,  # Keep snake_case
    )
    
    # Host validation with custom constraints
    host_id: Annotated[str, Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Target host for deployment"
    )]
    
    # Stack name with Docker Compose constraints
    stack_name: Annotated[str, Field(
        min_length=1,
        max_length=63,  # Docker service name limit
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$",
        description="Stack name (must follow Docker naming rules)"
    )]
    
    # Compose content with size validation
    compose_content: Annotated[str, Field(
        min_length=50,      # Must be substantial
        max_length=1024*1024,  # 1MB limit
        description="Docker Compose YAML content"
    )]
    
    # Environment with secret detection
    environment: Annotated[dict[str, str], Field(
        default_factory=dict,
        description="Environment variables for the stack"
    )]
    
    # Boolean options with defaults
    pull_images: Annotated[bool, Field(
        default=True,
        description="Pull latest images before deployment"
    )]
    
    recreate: Annotated[bool, Field(
        default=False,
        description="Recreate containers even if no changes"
    )]
```

### Pydantic v2 Model Dependencies
- **Pydantic v2**: Latest validation and serialization features
- **ConfigDict**: Modern configuration approach (replaces Config class)
- **Annotated**: Python 3.9+ annotation metadata
- **Field**: Enhanced field configuration with better validation
- **Literal**: Type-safe enum-like constants
- **computed fields**: Dynamic properties with @property or computed_field

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

## Modern Validation Patterns (Pydantic v2)

### Field-Level Validation with Annotated
```python
from typing import Annotated
from pydantic import Field, field_validator, model_validator

# Modern constraint-based validation
class HostInfo(BaseModel):
    """Host configuration with comprehensive validation."""
    
    # Port validation with constraints
    port: Annotated[int, Field(
        ge=1, le=65535,
        description="Valid TCP port number",
        json_schema_extra={"examples": [22, 80, 443, 8080]}
    )] = 22
    
    # Hostname with pattern validation
    hostname: Annotated[str, Field(
        min_length=1,
        max_length=253,  # RFC limit
        pattern=r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$",
        description="Valid hostname or IP address"
    )]
    
    # SSH user validation
    user: Annotated[str, Field(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z_][a-z0-9_-]*$",
        description="Valid Unix username"
    )]
    
    # Optional SSH key path validation
    ssh_key_path: Annotated[str | None, Field(
        default=None,
        min_length=1,
        max_length=4096,
        description="Path to SSH private key file"
    )]
    
    # Model-level field validator (Pydantic v2 syntax)
    @field_validator('port')
    @classmethod
    def validate_port_not_reserved(cls, v: int) -> int:
        """Ensure port is not a system reserved port below 1024 unless common."""
        common_system_ports = {22, 80, 443}
        if v < 1024 and v not in common_system_ports:
            raise ValueError(f'Port {v} is reserved. Use ports > 1024 or common ports: {common_system_ports}')
        return v
    
    # Model validator for cross-field validation (Pydantic v2)
    @model_validator(mode='after')
    def validate_ssh_key_exists(self) -> 'HostInfo':
        """Validate SSH key file exists if specified."""
        if self.ssh_key_path:
            import os
            if not os.path.exists(self.ssh_key_path):
                raise ValueError(f'SSH key file not found: {self.ssh_key_path}')
            
            # Check key file permissions
            stat_info = os.stat(self.ssh_key_path)
            if stat_info.st_mode & 0o077:  # Check if group/other have any permissions
                raise ValueError(f'SSH key file has insecure permissions: {self.ssh_key_path}')
        
        return self

# Advanced validation with custom types
from pydantic import BeforeValidator, PlainValidator
from typing import Any

def validate_memory_size(value: Any) -> int:
    """Convert memory size strings to bytes."""
    if isinstance(value, str):
        value = value.lower().strip()
        units = {
            'b': 1,
            'kb': 1024, 'k': 1024,
            'mb': 1024**2, 'm': 1024**2,
            'gb': 1024**3, 'g': 1024**3,
            'tb': 1024**4, 't': 1024**4
        }
        
        for unit, multiplier in units.items():
            if value.endswith(unit):
                size_str = value[:-len(unit)].strip()
                try:
                    return int(float(size_str) * multiplier)
                except ValueError:
                    break
        
        # Try parsing as plain number
        try:
            return int(value)
        except ValueError:
            raise ValueError(f'Invalid memory size format: {value}')
    
    return int(value)

MemorySize = Annotated[int, BeforeValidator(validate_memory_size)]

class ContainerConstraints(BaseModel):
    """Container resource constraints with smart parsing."""
    
    # Memory limit with smart parsing
    memory_limit: MemorySize | None = Field(
        default=None,
        description="Memory limit (supports: 512m, 1g, 2gb, 1024, etc.)"
    )
    
    # CPU limit with validation
    cpu_limit: Annotated[float | None, Field(
        default=None,
        ge=0.001,  # Minimum 0.1% CPU
        le=64.0,   # Maximum 64 CPUs
        description="CPU limit in cores (e.g., 0.5, 1.5, 2.0)"
    )]
    
    # Custom field validator
    @field_validator('memory_limit', mode='after')
    @classmethod
    def validate_reasonable_memory(cls, v: int | None) -> int | None:
        """Ensure memory limit is reasonable."""
        if v is not None:
            if v < 4 * 1024 * 1024:  # 4MB minimum
                raise ValueError('Memory limit too small (minimum 4MB)')
            if v > 1024 * 1024 * 1024 * 1024:  # 1TB maximum
                raise ValueError('Memory limit too large (maximum 1TB)')
        return v
```

### Async Validation (Pydantic v2)
```python
import asyncio
from pydantic import field_validator, ValidationInfo

class AsyncValidatedHost(BaseModel):
    """Host model with async validation capabilities."""
    
    hostname: str
    port: int = 22
    
    @field_validator('hostname')
    @classmethod
    def validate_hostname_format(cls, v: str) -> str:
        """Sync validation for hostname format."""
        if not v or len(v) > 253:
            raise ValueError('Invalid hostname length')
        return v
    
    # For async validation, use custom methods called after model creation
    async def async_validate_connectivity(self, timeout: float = 5.0) -> bool:
        """Async method to validate host connectivity."""
        try:
            import asyncio
            import socket
            
            # Create connection with timeout
            future = asyncio.open_connection(self.hostname, self.port)
            reader, writer = await asyncio.wait_for(future, timeout=timeout)
            writer.close()
            await writer.wait_closed()
            return True
            
        except Exception:
            return False
    
    async def async_validate_docker_available(self) -> bool:
        """Check if Docker is available on the host."""
        # This would use SSH or Docker context to check
        # Implementation depends on your context manager
        return True  # Placeholder

# Usage pattern for async validation
async def create_validated_host(hostname: str, port: int = 22) -> AsyncValidatedHost:
    """Create host with full async validation."""
    # Create model (sync validation)
    host = AsyncValidatedHost(hostname=hostname, port=port)
    
    # Perform async validation
    if not await host.async_validate_connectivity():
        raise ValueError(f'Cannot connect to {hostname}:{port}')
    
    if not await host.async_validate_docker_available():
        raise ValueError(f'Docker not available on {hostname}')
    
    return host
```

## Modern Type Features (Python 3.10+)

### Generic Models with TypeVars
```python
from typing import TypeVar, Generic
from pydantic import BaseModel

T = TypeVar('T')
U = TypeVar('U')

class OperationResult(BaseModel, Generic[T]):
    """Generic result model for type-safe operations."""
    
    success: bool
    data: T | None = None
    error: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)
    
    # Type-safe factory methods
    @classmethod
    def success_result(cls, data: T) -> 'OperationResult[T]':
        return cls(success=True, data=data)
    
    @classmethod
    def error_result(cls, error: str) -> 'OperationResult[T]':
        return cls(success=False, error=error)

# Usage with full type safety
container_result: OperationResult[ContainerInfo] = OperationResult.success_result(
    ContainerInfo(container_id="abc123", name="web", host_id="prod-1")
)

stats_result: OperationResult[list[ContainerStats]] = OperationResult.success_result([
    ContainerStats(container_id="abc123", host_id="prod-1")
])

# Type-safe access
if container_result.success:
    container: ContainerInfo | None = container_result.data  # Type checker knows this
```

### Union Types with Discriminated Models
```python
from typing import Literal, Union
from pydantic import BaseModel, Field, Discriminator

class DockerEvent(BaseModel):
    """Base class for Docker events."""
    timestamp: datetime
    host_id: str

class ContainerStartEvent(DockerEvent):
    """Container started event."""
    event_type: Literal["container_start"] = "container_start"
    container_id: str
    container_name: str

class ContainerStopEvent(DockerEvent):
    """Container stopped event."""  
    event_type: Literal["container_stop"] = "container_stop"
    container_id: str
    exit_code: int | None = None

class StackDeployEvent(DockerEvent):
    """Stack deployment event."""
    event_type: Literal["stack_deploy"] = "stack_deploy"
    stack_name: str
    services: list[str]
    success: bool

# Discriminated union for type safety
DockerEventUnion = Union[
    ContainerStartEvent,
    ContainerStopEvent,
    StackDeployEvent
]

class EventLog(BaseModel):
    """Event log with discriminated union types."""
    events: list[DockerEventUnion] = Field(
        discriminator='event_type',  # Pydantic v2 discriminated unions
        default_factory=list
    )
    
    def add_event(self, event: DockerEventUnion) -> None:
        """Add event with type safety."""
        self.events.append(event)
    
    def get_container_events(self) -> list[ContainerStartEvent | ContainerStopEvent]:
        """Get only container-related events with type narrowing."""
        return [
            event for event in self.events 
            if isinstance(event, (ContainerStartEvent, ContainerStopEvent))
        ]
```

### Computed Fields and Properties (Pydantic v2)
```python
from pydantic import computed_field
from functools import cached_property

class EnhancedContainerInfo(BaseModel):
    """Container info with computed properties."""
    
    container_id: str
    name: str
    host_id: str
    status: Literal["created", "restarting", "running", "removing", "paused", "exited", "dead"]
    memory_usage: int | None = None  # bytes
    memory_limit: int | None = None  # bytes
    cpu_percentage: float | None = None
    
    # Computed field (serialized)
    @computed_field
    @property
    def is_healthy(self) -> bool:
        """Computed field indicating if container is healthy."""
        if self.status != "running":
            return False
        
        if self.cpu_percentage and self.cpu_percentage > 90:
            return False
            
        if (self.memory_usage and self.memory_limit and 
            self.memory_usage / self.memory_limit > 0.95):
            return False
        
        return True
    
    # Regular property (not serialized)
    @property
    def memory_usage_percentage(self) -> float | None:
        """Memory usage as percentage (not serialized)."""
        if self.memory_usage and self.memory_limit:
            return (self.memory_usage / self.memory_limit) * 100
        return None
    
    # Cached property for expensive computations
    @cached_property
    def resource_score(self) -> float:
        """Complex resource health score (cached)."""
        score = 100.0
        
        if self.status != "running":
            return 0.0
        
        if self.cpu_percentage:
            score -= max(0, self.cpu_percentage - 50)  # Penalty above 50%
        
        if self.memory_usage_percentage:
            score -= max(0, self.memory_usage_percentage - 70)  # Penalty above 70%
        
        return max(0.0, min(100.0, score))

# Serialization includes computed fields
container = EnhancedContainerInfo(
    container_id="abc123",
    name="web-server", 
    host_id="prod-1",
    status="running",
    memory_usage=512 * 1024 * 1024,
    memory_limit=1024 * 1024 * 1024,
    cpu_percentage=25.5
)

data = container.model_dump()  # Includes 'is_healthy' computed field
print(data["is_healthy"])      # True
print(container.resource_score)  # 74.5 (cached after first access)
```

### Advanced Serialization Control
```python
from pydantic import BaseModel, Field, field_serializer, model_serializer
from typing import Any

class AdvancedContainerStats(BaseModel):
    """Container stats with advanced serialization control."""
    
    container_id: str
    host_id: str
    memory_usage: int | None = None  # bytes
    memory_limit: int | None = None  # bytes
    network_rx: int | None = None    # bytes
    network_tx: int | None = None    # bytes
    uptime: float | None = None      # seconds
    
    # Custom field serialization
    @field_serializer('memory_usage', 'memory_limit', 'network_rx', 'network_tx')
    def serialize_bytes(self, value: int | None) -> dict[str, Any] | None:
        """Serialize byte values with human-readable format."""
        if value is None:
            return None
        
        return {
            "bytes": value,
            "human": self._format_bytes(value)
        }
    
    @field_serializer('uptime')
    def serialize_uptime(self, value: float | None) -> dict[str, Any] | None:
        """Serialize uptime with human-readable format."""
        if value is None:
            return None
        
        return {
            "seconds": value,
            "human": self._format_duration(value)
        }
    
    # Custom model serialization
    @model_serializer(mode='wrap')
    def serialize_model(self, serializer, info):
        """Custom model serialization with metadata."""
        # Get default serialization
        data = serializer(self)
        
        # Add metadata
        data['_metadata'] = {
            'serialized_at': datetime.now().isoformat(),
            'version': '1.0',
            'host_id': self.host_id
        }
        
        return data
    
    def _format_bytes(self, bytes_value: int) -> str:
        """Format bytes to human-readable string."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_value < 1024.0:
                return f"{bytes_value:.1f}{unit}"
            bytes_value /= 1024.0
        return f"{bytes_value:.1f}PB"
    
    def _format_duration(self, seconds: float) -> str:
        """Format duration to human-readable string."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            return f"{seconds/60:.1f}m"
        elif seconds < 86400:
            return f"{seconds/3600:.1f}h"
        else:
            return f"{seconds/86400:.1f}d"

# Example usage
stats = AdvancedContainerStats(
    container_id="abc123",
    host_id="prod-1",
    memory_usage=512 * 1024 * 1024,  # 512MB
    uptime=3665.5  # ~1 hour
)

serialized = stats.model_dump()
# Output includes:
# {
#   "memory_usage": {"bytes": 536870912, "human": "512.0MB"},
#   "uptime": {"seconds": 3665.5, "human": "1.0h"},
#   "_metadata": {"serialized_at": "2024-01-01T12:00:00", ...}
# }
```

## Model Usage Patterns

### Modern Creation and Serialization (Pydantic v2)
```python
# Create model with enhanced validation
stats = ContainerStats(
    container_id="abc123",
    host_id="production-1",
    cpu_percentage=45.2,
    memory_usage=512 * 1024 * 1024  # 512MB in bytes
)

# Pydantic v2 serialization methods
return stats.model_dump()                    # Dictionary with all fields
return stats.model_dump(exclude={'host_id'}) # Exclude specific fields
return stats.model_dump(include={'cpu_percentage', 'memory_usage'})  # Include only specific

# JSON serialization with options
json_data = stats.model_dump_json(
    exclude_none=True,      # Don't include None values
    by_alias=True,          # Use field aliases if defined
    indent=2                # Pretty printing
)

# Model copying with updates
updated_stats = stats.model_copy(update={'cpu_percentage': 50.0})
```

### Type-Safe Model Composition
```python
# Models with nested type safety
class TypedPortListResponse(BaseModel, Generic[T]):
    """Type-safe port list response."""
    
    host_id: str
    port_mappings: list[PortMapping]
    additional_data: T | None = None  # Generic additional data
    
    # Type-safe methods
    def get_port_by_number(self, port: int) -> PortMapping | None:
        """Get port mapping by port number with type safety."""
        return next(
            (mapping for mapping in self.port_mappings if mapping.host_port == str(port)),
            None
        )

class PortMapping(BaseModel):
    """Individual port mapping with validation."""
    
    host_port: Annotated[str, Field(pattern=r"^\d+$")]
    container_port: Annotated[str, Field(pattern=r"^\d+(/tcp|/udp)?$")]
    protocol: Literal["tcp", "udp"] = "tcp"
    
    @computed_field
    @property
    def port_number(self) -> int:
        """Get port as integer."""
        return int(self.host_port)
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
