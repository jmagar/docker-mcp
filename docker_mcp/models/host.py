"""Host-related data models."""

from pydantic import BaseModel, Field


class HostInfo(BaseModel):
    """Information about a Docker host."""

    host_id: str
    hostname: str
    user: str
    port: int = 22
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    connected: bool = False
    docker_version: str | None = None
    last_ping: str | None = None


class HostStatus(BaseModel):
    """Status information for a Docker host."""

    host_id: str
    online: bool
    ssh_connected: bool
    docker_connected: bool
    error_message: str | None = None
    last_check: str
    response_time_ms: float | None = None


class HostResources(BaseModel):
    """Resource information for a Docker host."""

    host_id: str
    cpu_count: int | None = None
    memory_total: int | None = None  # bytes
    memory_available: int | None = None  # bytes
    disk_total: int | None = None  # bytes
    disk_available: int | None = None  # bytes
    load_average: list[float] | None = None
    containers_running: int = 0
    containers_total: int = 0
    images_count: int = 0


class AddHostRequest(BaseModel):
    """Request to add a new Docker host."""

    host_id: str
    ssh_host: str
    ssh_user: str
    ssh_port: int = 22
    ssh_key_path: str | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    test_connection: bool = True
