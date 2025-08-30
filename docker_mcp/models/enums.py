"""Enum definitions for Docker MCP tools."""

from enum import Enum


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
    BUILD = "build"
    LOGS = "logs"
    REMOVE = "remove"  # Added for test cleanup


class ComposeAction(Enum):
    """Actions for the docker_compose tool."""

    LIST = "list"
    VIEW = "view"
    DEPLOY = "deploy"
    UP = "up"
    DOWN = "down"
    RESTART = "restart"
    BUILD = "build"
    LOGS = "logs"
    MIGRATE = "migrate"


class Protocol(Enum):
    """Network protocols for port operations."""

    TCP = "TCP"
    UDP = "UDP"
