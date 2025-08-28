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
    # Cache-powered advanced search actions
    SEARCH_STACK = "search_stack"           # Find containers by compose project
    SEARCH_LABEL = "search_label"           # Find containers by labels
    SEARCH_CROSS_HOST = "search_cross_host" # Search containers across all hosts  
    SEARCH_MOUNTS = "search_mounts"         # Find containers using specific mounts
    SEARCH_STATUS = "search_status"         # Find containers by status
    HEALTH_SUMMARY = "health_summary"       # Get health status summary
    RESOURCE_USAGE = "resource_usage"       # Get resource usage summary


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