"""Common parameter models for FastMCP tools."""

from typing import Literal

from pydantic import BaseModel, Field


class HostIdentifier(BaseModel):
    """Host identifier with validation."""

    host_id: str = Field(
        ...,
        description="Docker host identifier",
        min_length=1,
        max_length=100,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )


class ContainerIdentifier(BaseModel):
    """Container identifier with validation."""

    container_id: str = Field(..., description="Container ID or name", min_length=1, max_length=100)


class PaginationParams(BaseModel):
    """Pagination parameters for list operations."""

    limit: int = Field(default=20, description="Maximum number of results to return", ge=1, le=1000)
    offset: int = Field(default=0, description="Number of results to skip", ge=0)


class TimeoutParams(BaseModel):
    """Timeout and force parameters for operations."""

    timeout: int = Field(default=10, description="Operation timeout in seconds", ge=1, le=300)
    force: bool = Field(default=False, description="Force the operation")


class LogParams(BaseModel):
    """Log retrieval parameters."""

    follow: bool = Field(default=False, description="Follow log output")
    lines: int = Field(default=100, description="Number of log lines to retrieve", ge=1, le=10000)


class PortParams(BaseModel):
    """Port-related parameters."""

    port: int = Field(..., description="Port number", ge=1, le=65535)
    protocol: Literal["TCP", "UDP"] = Field(default="TCP", description="Protocol type")


class StackActionParams(BaseModel):
    """Stack management action parameters."""

    action: Literal["up", "down", "restart", "logs", "ps"] = Field(
        ..., description="Stack management action"
    )


class ContainerActionParams(BaseModel):
    """Container management action parameters."""

    action: Literal["list", "info", "start", "stop", "restart", "build", "logs"] = Field(
        ..., description="Container management action"
    )


class CleanupParams(BaseModel):
    """Docker cleanup parameters."""

    cleanup_type: Literal["check", "safe", "moderate", "aggressive"] = Field(
        ..., description="Cleanup level"
    )


class PortFilterParams(BaseModel):
    """Port filtering and export parameters."""

    include_stopped: bool = Field(default=False, description="Include stopped containers")
    export_format: Literal["json", "csv", "markdown"] | None = Field(
        default=None, description="Export format for results"
    )
    filter_project: str = Field(default="", description="Filter by compose project name")
    filter_range: str = Field(
        default="", description="Filter by port range (e.g., '8000-9000' or '80')"
    )
    filter_protocol: Literal["TCP", "UDP"] | None = Field(
        default=None, description="Filter by protocol"
    )
    scan_available: bool = Field(default=False, description="Scan for available ports in range")
    suggest_next: bool = Field(default=False, description="Suggest next available port")
    use_cache: bool = Field(default=True, description="Use cached data when available")
