"""Parameter models for FastMCP tool validation."""

from typing import Literal

from pydantic import BaseModel, Field, computed_field


class DockerHostsParams(BaseModel):
    """Parameters for the docker_hosts consolidated tool."""

    action: Literal["list", "add", "ports", "compose_path", "import_ssh", "cleanup", "disk_usage", "schedule", "reserve_port", "release_port", "list_reservations"] = Field(
        ...,
        description="Action to perform"
    )
    host_id: str = Field(
        default="",
        min_length=1,
        description="Host identifier"
    )
    ssh_host: str = Field(
        default="",
        min_length=1,
        description="SSH hostname or IP address"
    )
    ssh_user: str = Field(
        default="",
        min_length=1,
        description="SSH username"
    )
    ssh_port: int = Field(
        default=22,
        ge=1,
        le=65535,
        description="SSH port number"
    )
    ssh_key_path: str | None = Field(
        default=None,
        description="Path to SSH private key file"
    )
    description: str = Field(
        default="",
        description="Host description"
    )
    tags: list[str] | None = Field(
        default_factory=list,
        description="Host tags"
    )
    test_connection: bool = Field(
        default=True,
        description="Test connection when adding host"
    )
    include_stopped: bool = Field(
        default=False,
        description="Include stopped containers in listings"
    )
    compose_path: str | None = Field(
        default=None,
        description="Docker Compose file path"
    )
    enabled: bool = Field(
        default=True,
        description="Whether host is enabled"
    )
    ssh_config_path: str | None = Field(
        default=None,
        description="Path to SSH config file"
    )
    selected_hosts: str | None = Field(
        default=None,
        description="Comma-separated list of hosts to select"
    )
    compose_path_overrides: dict[str, str] | None = Field(
        default_factory=dict,
        description="Per-host compose path overrides"
    )
    auto_confirm: bool = Field(
        default=False,
        description="Auto-confirm operations without prompting"
    )
    cleanup_type: Literal["check", "safe", "moderate", "aggressive"] | None = Field(
        default=None,
        description="Type of cleanup to perform"
    )
    schedule_action: Literal["add", "remove", "list", "enable", "disable"] | None = Field(
        default=None,
        description="Schedule management action"
    )
    schedule_frequency: Literal["daily", "weekly", "monthly", "custom"] | None = Field(
        default=None,
        description="Cleanup frequency"
    )
    schedule_time: str | None = Field(
        default=None,
        pattern=r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$",
        description="Time to run cleanup in HH:MM format (24-hour)"
    )
    schedule_id: str | None = Field(
        default=None,
        description="Schedule identifier for management"
    )

    # Enhanced port action parameters
    export_format: Literal["json", "csv", "markdown"] | None = Field(
        default=None,
        description="Export format for port data"
    )
    filter_project: str | None = Field(
        default=None,
        description="Filter by compose project name"
    )
    filter_range: str | None = Field(
        default=None,
        description="Filter by port range (e.g., '8000-9000')"
    )
    filter_protocol: Literal["TCP", "UDP"] | None = Field(
        default=None,
        description="Filter by protocol"
    )
    scan_available: bool = Field(
        default=False,
        description="Scan for truly available ports"
    )
    suggest_next: bool = Field(
        default=False,
        description="Suggest next available port"
    )
    use_cache: bool = Field(
        default=True,
        description="Use cached data when available"
    )

    # Port reservation parameters
    port: int = Field(
        default=0,
        ge=1,
        le=65535,
        description="Port number for reservation operations"
    )
    protocol: Literal["TCP", "UDP"] = Field(
        default="TCP",
        description="Protocol type for port reservation"
    )
    service_name: str = Field(
        default="",
        min_length=1,
        description="Service name for port reservation"
    )
    reserved_by: str = Field(
        default="user",
        description="Who is reserving the port"
    )
    expires_days: int | None = Field(
        default=None,
        ge=1,
        description="Days until reservation expires (None for permanent)"
    )
    notes: str = Field(
        default="",
        description="Notes for the reservation"
    )

    @computed_field(return_type=list[str])
    @property
    def selected_hosts_list(self) -> list[str]:
        if not self.selected_hosts:
            return []
        return [h.strip() for h in self.selected_hosts.split(",") if h.strip()]


class DockerContainerParams(BaseModel):
    """Parameters for the docker_container consolidated tool."""

    action: Literal["list", "info", "start", "stop", "restart", "build", "logs", "pull"] = Field(
        ...,
        description="Action to perform"
    )
    host_id: str = Field(
        default="",
        min_length=1,
        description="Host identifier"
    )
    container_id: str = Field(
        default="",
        min_length=1,
        description="Container identifier"
    )
    all_containers: bool = Field(
        default=False,
        description="Include all containers (not just running ones)"
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=1000,
        description="Maximum number of results to return"
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of results to skip"
    )
    follow: bool = Field(
        default=False,
        description="Follow log output"
    )
    lines: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Number of log lines to retrieve"
    )
    force: bool = Field(
        default=False,
        description="Force the operation"
    )
    timeout: int = Field(
        default=10,
        ge=1,
        le=300,
        description="Operation timeout in seconds"
    )


class DockerComposeParams(BaseModel):
    """Parameters for the docker_compose consolidated tool."""

    action: Literal["list", "deploy", "up", "down", "restart", "build", "discover", "logs", "migrate"] = Field(
        ...,
        description="Action to perform"
    )
    host_id: str = Field(
        default="",
        min_length=1,
        description="Host identifier"
    )
    stack_name: str = Field(
        default="",
        min_length=1,
        description="Stack name"
    )
    compose_content: str = Field(
        default="",
        description="Docker Compose file content"
    )
    environment: dict[str, str] | None = Field(
        default_factory=dict,
        description="Environment variables"
    )
    pull_images: bool = Field(
        default=True,
        description="Pull images before deploying"
    )
    recreate: bool = Field(
        default=False,
        description="Recreate containers"
    )
    follow: bool = Field(
        default=False,
        description="Follow log output"
    )
    lines: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Number of log lines to retrieve"
    )
    dry_run: bool = Field(
        default=False,
        description="Perform a dry run without making changes"
    )
    options: dict[str, str] | None = Field(
        default=None,
        description="Additional options for the operation"
    )
    target_host_id: str = Field(
        default="",
        min_length=1,
        description="Target host ID for migration operations"
    )
    remove_source: bool = Field(
        default=False,
        description="Remove source stack after migration"
    )
    skip_stop_source: bool = Field(
        default=False,
        description="Skip stopping source stack before migration"
    )
    start_target: bool = Field(
        default=True,
        description="Start target stack after migration"
    )
