"""Parameter models for FastMCP tool validation."""

from typing import Any, Literal

from pydantic import Field, computed_field, field_validator

from .container import MCPModel

# Import Enum types
from .enums import ComposeAction, ContainerAction, HostAction


def _validate_enum_action(value: Any, enum_class: type) -> Any:
    """Generic validator for enum action fields."""
    if isinstance(value, str):
        # Handle "EnumClass.VALUE" format
        if "." in value:
            enum_value = value.split(".")[-1].lower()
        else:
            enum_value = value.lower()

        # Match by value or name
        for action in enum_class:
            if action.value == enum_value or action.name.lower() == enum_value:
                return action
    elif isinstance(value, enum_class):
        return value

    # Let Pydantic handle the error if no match
    return value


class DockerHostsParams(MCPModel):
    """Parameters for the docker_hosts consolidated tool."""

    action: HostAction = Field(
        default=HostAction.LIST, description="Action to perform (defaults to list if not provided)"
    )
    host_id: str = Field(default="", description="Host identifier")
    ssh_host: str = Field(default="", description="SSH hostname or IP address")
    ssh_user: str = Field(default="", description="SSH username")
    ssh_port: int = Field(default=22, ge=1, le=65535, description="SSH port number")
    ssh_key_path: str | None = Field(default=None, description="Path to SSH private key file")
    description: str = Field(default="", description="Host description")
    tags: list[str] | None = Field(default_factory=list, description="Host tags")
    compose_path: str | None = Field(default=None, description="Docker Compose file path")
    appdata_path: str | None = Field(default=None, description="Application data storage path")
    enabled: bool = Field(default=True, description="Whether host is enabled")
    zfs_capable: bool = Field(default=False, description="Whether host has ZFS available")
    zfs_dataset: str = Field(default="", description="ZFS dataset path for appdata")
    ssh_config_path: str | None = Field(default=None, description="Path to SSH config file")
    selected_hosts: str | None = Field(
        default=None, description="Comma-separated list of hosts to select"
    )
    cleanup_type: Literal["check", "safe", "moderate", "aggressive"] | None = Field(
        default=None, description="Type of cleanup to perform"
    )
    frequency: Literal["daily", "weekly", "monthly", "custom"] | None = Field(
        default=None, description="Cleanup schedule frequency"
    )
    time: str | None = Field(
        default=None,
        pattern=r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$",
        description="Cleanup schedule time in HH:MM format (24-hour)",
    )

    # Port check parameter (only for ports check sub-action)
    port: int = Field(
        default=0,
        ge=0,
        le=65535,
        description="Port number to check availability (only for ports check)",
    )

    @computed_field(return_type=list[str])
    @property
    def selected_hosts_list(self) -> list[str]:
        if not self.selected_hosts:
            return []
        return [h.strip() for h in self.selected_hosts.split(",") if h.strip()]

    @field_validator("action", mode="before")
    @classmethod
    def validate_action(cls, v):
        """Validate action field to handle various enum input formats."""
        return _validate_enum_action(v, HostAction)


class DockerContainerParams(MCPModel):
    """Parameters for the docker_container consolidated tool."""

    action: ContainerAction = Field(..., description="Action to perform")
    host_id: str = Field(default="", description="Host identifier")
    container_id: str = Field(default="", description="Container identifier")
    all_containers: bool = Field(
        default=False, description="Include all containers (not just running ones)"
    )
    limit: int = Field(default=20, ge=1, le=1000, description="Maximum number of results to return")
    offset: int = Field(default=0, ge=0, description="Number of results to skip")
    follow: bool = Field(default=False, description="Follow log output")
    lines: int = Field(default=100, ge=1, le=10000, description="Number of log lines to retrieve")
    force: bool = Field(default=False, description="Force the operation")
    timeout: int = Field(default=10, ge=1, le=300, description="Operation timeout in seconds")

    @field_validator("action", mode="before")
    @classmethod
    def validate_action(cls, v):
        """Validate action field to handle various enum input formats."""
        return _validate_enum_action(v, ContainerAction)


class DockerComposeParams(MCPModel):
    """Parameters for the docker_compose consolidated tool."""

    action: ComposeAction = Field(..., description="Action to perform")
    host_id: str = Field(default="", description="Host identifier")
    stack_name: str = Field(
        default="",
        max_length=63,
        pattern=r"^$|^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
        description="Stack name (DNS-compliant: lowercase letters, numbers, hyphens; no underscores)",
    )
    compose_content: str = Field(default="", description="Docker Compose file content")
    environment: dict[str, str] | None = Field(
        default_factory=dict, description="Environment variables"
    )
    pull_images: bool = Field(default=True, description="Pull images before deploying")
    recreate: bool = Field(default=False, description="Recreate containers")
    follow: bool = Field(default=False, description="Follow log output")
    lines: int = Field(default=100, ge=1, le=10000, description="Number of log lines to retrieve")
    dry_run: bool = Field(default=False, description="Perform a dry run without making changes")
    options: dict[str, str] | None = Field(
        default=None, description="Additional options for the operation"
    )
    target_host_id: str = Field(default="", description="Target host ID for migration operations")
    remove_source: bool = Field(default=False, description="Remove source stack after migration")
    skip_stop_source: bool = Field(
        default=False, description="Skip stopping source stack before migration"
    )
    start_target: bool = Field(default=True, description="Start target stack after migration")

    @field_validator("action", mode="before")
    @classmethod
    def validate_action(cls, v):
        """Validate action field to handle various enum input formats."""
        return _validate_enum_action(v, ComposeAction)
