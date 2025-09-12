"""Enum definitions for Docker MCP tools."""

from enum import Enum
from typing import Literal

# Type aliases
ProtocolLiteral = Literal["tcp", "udp", "sctp"]
CleanupType = Literal["check", "safe", "moderate", "aggressive"]
ScheduleFrequency = Literal["daily", "weekly", "monthly", "custom"]


class HostAction(Enum):
    """Actions for the docker_hosts tool."""

    LIST = "list"
    ADD = "add"
    EDIT = "edit"
    REMOVE = "remove"
    TEST_CONNECTION = "test_connection"
    DISCOVER = "discover"
    PORTS = "ports"
    IMPORT_SSH = "import_ssh"
    CLEANUP = "cleanup"


class ContainerAction(Enum):
    """Actions for the docker_container tool."""

    LIST = "list"
    INFO = "info"
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    LOGS = "logs"
    REMOVE = "remove"  # Added for test cleanup


class ComposeAction(Enum):
    """Actions for the docker_compose tool."""

    LIST = "list"
    DISCOVER = "discover"
    VIEW = "view"
    DEPLOY = "deploy"
    UP = "up"
    DOWN = "down"
    RESTART = "restart"
    BUILD = "build"
    LOGS = "logs"
    MIGRATE = "migrate"
    PULL = "pull"


# Removed unused Protocol enum; protocol strings are handled directly where needed.
