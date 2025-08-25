"""Parameter models for FastMCP tool validation."""

from typing import Literal

from pydantic import BaseModel, Field, computed_field


class DockerHostsParams(BaseModel):
    """Parameters for the docker_hosts consolidated tool."""

    action: Literal["list", "add", "ports", "compose_path", "import_ssh", "cleanup", "disk_usage", "schedule"] = Field(
        ...,
        description="Action to perform"
    )
    host_id: str = Field(
        default="",
        description="Host identifier"
    )
    ssh_host: str = Field(
        default="",
        description="SSH hostname or IP address"
    )
    ssh_user: str = Field(
        default="",
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
    tags: list[str] = Field(
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
    compose_path_overrides: dict[str, str] = Field(
        default_factory=dict,
        description="Per-host compose path overrides"
    )
    auto_confirm: bool = Field(
        default=False,
        description="Auto-confirm operations without prompting"
    )
    cleanup_type: str | None = Field(
        default=None,
        description="Type of cleanup to perform (check, safe, moderate, aggressive) **(used by: cleanup)**"
    )
    schedule_action: str | None = Field(
        default=None,
        description="Schedule action to perform (add, remove, list, enable, disable) **(used by: schedule)**"
    )
    schedule_frequency: str | None = Field(
        default=None,
        description="Cleanup frequency (daily, weekly, monthly, custom) **(used by: schedule add)**"
    )
    schedule_time: str | None = Field(
        default=None,
        description="Time to run cleanup (e.g., '02:00') **(used by: schedule add)**"
    )
    schedule_id: str | None = Field(
        default=None,
        description="Schedule identifier for management **(used by: schedule remove/enable/disable)**"
    )

    @computed_field(return_type=list[str])
    @property
    def selected_hosts_list(self) -> list[str]:
        if not self.selected_hosts:
            return []
        return [h.strip() for h in self.selected_hosts.split(",") if h.strip()]


class DockerContainerParams(BaseModel):
    """Parameters for the docker_container consolidated tool."""

    action: str = Field(
        ...,
        description="Action to perform (list, info, start, stop, restart, build, logs, pull)"
    )
    host_id: str = Field(
        default="",
        description="Host identifier"
    )
    container_id: str = Field(
        default="",
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

    action: str = Field(
        ...,
        description="Action to perform (list, deploy, up, down, restart, build, discover, logs, migrate)"
    )
    host_id: str = Field(
        default="",
        description="Host identifier"
    )
    stack_name: str = Field(
        default="",
        description="Stack name"
    )
    compose_content: str = Field(
        default="",
        description="Docker Compose file content"
    )
    environment: dict[str, str] = Field(
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
