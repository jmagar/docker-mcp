"""Container-related data models."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from .enums import ProtocolLiteral


class MCPModel(BaseModel):
    """Base model with common MCP settings."""

    def model_dump(self, **kwargs) -> dict[str, Any]:
        """Convert to dict with exclude_none by default."""
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(**kwargs)


# Minimal Pydantic models for type safety (matches current dict shapes)
class ContainerInfo(MCPModel):
    """Information about a Docker container (minimal for type safety)."""

    container_id: str
    name: str
    host_id: str
    image: str | None = None
    status: str | None = None
    state: str | None = None
    ports: list[str] = Field(default_factory=list)


class ContainerStats(MCPModel):
    """Resource statistics for a container."""

    container_id: str
    host_id: str
    cpu_percentage: float | None = None
    memory_usage: int | None = None  # bytes
    memory_limit: int | None = None  # bytes
    memory_percentage: float | None = None
    network_rx: int | None = None  # bytes
    network_tx: int | None = None  # bytes
    block_read: int | None = None  # bytes
    block_write: int | None = None  # bytes
    pids: int | None = None


class ContainerLogs(MCPModel):
    """Container log data."""

    container_id: str
    host_id: str
    logs: list[str]
    timestamp: datetime = Field(description="Log retrieval timestamp in ISO 8601 format")
    truncated: bool = False


class StackInfo(MCPModel):
    """Information about a Docker Compose stack."""

    name: str
    host_id: str
    services: list[str] = Field(default_factory=list)
    status: str
    created: datetime | None = Field(default=None, description="Creation timestamp in ISO 8601 format")
    updated: datetime | None = Field(default=None, description="Last update timestamp in ISO 8601 format")
    compose_file: str | None = None


# Minimal request model for type safety
class DeployStackRequest(MCPModel):
    """Request to deploy a Docker Compose stack (minimal for type safety)."""

    host_id: str
    stack_name: str
    compose_content: str
    environment: dict[str, str] = Field(default_factory=dict)
    pull_images: bool = True
    recreate: bool = False


class ContainerActionRequest(MCPModel):
    """Request to perform an action on a container."""

    host_id: str
    container_id: str
    action: Literal["start", "stop", "restart", "remove", "pause", "unpause"]
    force: bool = False


class LogStreamRequest(MCPModel):
    """Request to stream container logs."""

    host_id: str
    container_id: str
    follow: bool = True
    tail: int = 100
    since: str | None = None
    timestamps: bool = False


class PortMapping(MCPModel):
    """Individual port mapping with container context."""

    host_id: str
    host_ip: str
    host_port: Annotated[int, Field(ge=1, le=65535, description="Host port number")]
    container_port: Annotated[int, Field(ge=1, le=65535, description="Container port number")]
    protocol: ProtocolLiteral
    container_id: str
    container_name: str
    image: str
    compose_project: str | None = None
    is_conflict: bool = False
    conflict_with: list[str] = Field(default_factory=list)

    @field_validator('host_port', 'container_port', mode='before')
    @classmethod
    def parse_port_numbers(cls, v: str | int) -> int:
        """Parse and validate port numbers from strings or integers."""
        if isinstance(v, int):
            return v

        if isinstance(v, str):
            # Strip whitespace and parse
            v = v.strip()
            if not v:
                raise ValueError("Port number cannot be empty")

            try:
                port = int(v)
                if not (1 <= port <= 65535):
                    raise ValueError(f"Port {port} out of valid range 1-65535")
                return port
            except ValueError as e:
                if "invalid literal" in str(e):
                    raise ValueError(f"Invalid port number: '{v}' (must be numeric)") from e
                raise

        raise ValueError(f"Port must be string or integer, got {type(v)}")


class PortConflict(MCPModel):
    """Port conflict information."""

    host_id: str
    host_port: str
    protocol: ProtocolLiteral
    host_ip: str
    affected_containers: list[str]
    container_details: list[dict[str, Any]] = Field(default_factory=list)


class PortListResponse(MCPModel):
    """Port listing response (minimal for type safety)."""

    host_id: str
    total_ports: int
    total_containers: int
    port_mappings: list[PortMapping] = Field(default_factory=list)
    conflicts: list[PortConflict] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    timestamp: str | None = None  # ISO 8601 format
