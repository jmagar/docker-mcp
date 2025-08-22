"""Container-related data models."""

from typing import Any

from pydantic import BaseModel, Field


class ContainerInfo(BaseModel):
    """Information about a Docker container."""

    container_id: str
    name: str
    image: str
    status: str
    state: str
    created: str
    ports: list[dict[str, Any]] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    host_id: str


class ContainerStats(BaseModel):
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


class ContainerLogs(BaseModel):
    """Container log data."""

    container_id: str
    host_id: str
    logs: list[str]
    timestamp: str
    truncated: bool = False


class StackInfo(BaseModel):
    """Information about a Docker Compose stack."""

    name: str
    host_id: str
    services: list[str] = Field(default_factory=list)
    status: str
    created: str | None = None
    updated: str | None = None
    compose_file: str | None = None


class DeployStackRequest(BaseModel):
    """Request to deploy a Docker Compose stack."""

    host_id: str
    stack_name: str
    compose_content: str
    environment: dict[str, str] = Field(default_factory=dict)
    pull_images: bool = True
    recreate: bool = False


class ContainerAction(BaseModel):
    """Request to perform an action on a container."""

    host_id: str
    container_id: str
    action: str  # start, stop, restart, remove
    force: bool = False


class LogStreamRequest(BaseModel):
    """Request to stream container logs."""

    host_id: str
    container_id: str
    follow: bool = True
    tail: int = 100
    since: str | None = None
    timestamps: bool = False


class PortMapping(BaseModel):
    """Individual port mapping with container context."""

    host_ip: str
    host_port: str
    container_port: str
    protocol: str
    container_id: str
    container_name: str
    image: str
    compose_project: str | None = None
    is_conflict: bool = False
    conflict_with: list[str] = Field(default_factory=list)


class PortConflict(BaseModel):
    """Port conflict information."""

    host_port: str
    protocol: str
    host_ip: str
    affected_containers: list[str]
    container_details: list[dict[str, Any]] = Field(default_factory=list)


class PortListResponse(BaseModel):
    """Complete port listing response with summary and conflict detection."""

    host_id: str
    total_ports: int
    total_containers: int
    port_mappings: list[PortMapping]
    conflicts: list[PortConflict] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    timestamp: str
