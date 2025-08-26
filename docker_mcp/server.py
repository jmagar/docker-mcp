"""
FastMCP Docker SSH Manager Server

A production-ready FastMCP server for managing Docker containers and stacks
across multiple remote hosts via SSH connections.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from pydantic import Field

try:
    from .core.cache import PortCache
    from .core.config_loader import DockerMCPConfig, load_config
    from .core.docker_context import DockerContextManager
    from .core.file_watcher import HotReloadManager
    from .core.logging_config import get_server_logger, setup_logging
    from .middleware import (
        ErrorHandlingMiddleware,
        LoggingMiddleware,
        RateLimitingMiddleware,
        TimingMiddleware,
    )
    from .models.params import DockerComposeParams, DockerContainerParams, DockerHostsParams
    from .models.tool_params import (
        CleanupParams,
        ContainerActionParams,
        HostIdentifier,
        PaginationParams,
        PortFilterParams,
        PortParams,
        StackActionParams,
        TimeoutParams,
        LogParams,
    )
    from .resources import (
        DockerComposeResource,
        DockerContainersResource,
        DockerInfoResource,
        PortMappingResource,
    )
    from .services import ConfigService, ContainerService, HostService, StackService
    from .services.cleanup import CleanupService
    from .services.schedule import ScheduleService
    from .tools.logs import LogTools
except ImportError:
    from docker_mcp.core.cache import PortCache
    from docker_mcp.core.config_loader import DockerMCPConfig, load_config
    from docker_mcp.core.docker_context import DockerContextManager
    from docker_mcp.core.file_watcher import HotReloadManager
    from docker_mcp.core.logging_config import get_server_logger
    from docker_mcp.middleware import (
        ErrorHandlingMiddleware,
        LoggingMiddleware,
        RateLimitingMiddleware,
        TimingMiddleware,
    )
    from docker_mcp.resources import (
        DockerComposeResource,
        DockerContainersResource,
        DockerInfoResource,
        PortMappingResource,
    )
    from docker_mcp.services import ConfigService, ContainerService, HostService, StackService
    from docker_mcp.services.cleanup import CleanupService
    from docker_mcp.services.schedule import ScheduleService
    from docker_mcp.tools.logs import LogTools
    from docker_mcp.models.tool_params import (
        CleanupParams,
        ContainerActionParams,
        HostIdentifier,
        PaginationParams,
        PortFilterParams,
        PortParams,
        StackActionParams,
        TimeoutParams,
        LogParams,
    )


def get_data_dir() -> Path:
    """Get data directory based on environment with comprehensive validation.
    
    Priority order:
    1. FASTMCP_DATA_DIR (explicit override)
    2. DOCKER_MCP_DATA_DIR (application-specific)
    3. XDG_DATA_HOME (Linux/Unix standard)
    4. Container detection (/app/data)
    5. User home fallback (~/.docker-mcp/data)
    6. System temp fallback (/tmp/docker-mcp)
    """
    # Environment variable candidates in priority order
    env_candidates = [
        os.getenv("FASTMCP_DATA_DIR"),
        os.getenv("DOCKER_MCP_DATA_DIR"),
        os.getenv("XDG_DATA_HOME") and Path(os.getenv("XDG_DATA_HOME")) / "docker-mcp",
    ]

    # Check explicit environment overrides
    for candidate in env_candidates:
        if candidate:
            candidate_path = Path(candidate)
            # Validate the path can be created and is writable
            try:
                candidate_path.mkdir(parents=True, exist_ok=True)
                # Test write permissions
                test_file = candidate_path / ".write_test"
                test_file.touch()
                test_file.unlink()
                return candidate_path
            except (OSError, PermissionError, FileNotFoundError):
                # If we can't create/write, continue to next candidate
                continue

    # Check if running in container with comprehensive detection
    container_indicators = [
        os.getenv("DOCKER_CONTAINER", "").lower() in ("1", "true", "yes", "on"),
        os.path.exists("/.dockerenv"),
        os.path.exists("/app"),
        os.getenv("container") is not None,  # systemd container detection
    ]

    if any(container_indicators):
        container_path = Path("/app/data")
        try:
            container_path.mkdir(parents=True, exist_ok=True)
            return container_path
        except (OSError, PermissionError):
            # Container path failed, fall through to other options
            pass

    # Standard user data directory fallbacks
    fallback_candidates = [
        Path.home() / ".docker-mcp" / "data",  # Primary user directory
        Path.home() / ".local" / "share" / "docker-mcp",  # XDG-style fallback
        Path("/tmp") / "docker-mcp" / str(os.getuid() if hasattr(os, 'getuid') else 'user'),  # Temp with user isolation
        Path("/tmp") / "docker-mcp",  # Final fallback
    ]

    for fallback_path in fallback_candidates:
        try:
            fallback_path.mkdir(parents=True, exist_ok=True)
            # Test write permissions
            test_file = fallback_path / ".write_test"
            test_file.touch()
            test_file.unlink()
            return fallback_path
        except (OSError, PermissionError, FileNotFoundError):
            continue

    # If all else fails, return the primary fallback even if not writable
    # Let the calling code handle the permission error
    return Path.home() / ".docker-mcp" / "data"


def get_config_dir() -> Path:
    """Get config directory based on environment with comprehensive validation.
    
    Priority order:
    1. FASTMCP_CONFIG_DIR (explicit override)
    2. DOCKER_MCP_CONFIG_DIR (application-specific)
    3. XDG_CONFIG_HOME (Linux/Unix standard)
    4. Container detection (/app/config)
    5. Local project config (./config)
    6. User config fallback (~/.config/docker-mcp)
    7. System config fallback (/etc/docker-mcp)
    """
    # Environment variable candidates in priority order
    env_candidates = [
        os.getenv("FASTMCP_CONFIG_DIR"),
        os.getenv("DOCKER_MCP_CONFIG_DIR"),
        os.getenv("XDG_CONFIG_HOME") and Path(os.getenv("XDG_CONFIG_HOME")) / "docker-mcp",
    ]

    # Check explicit environment overrides
    for candidate in env_candidates:
        if candidate:
            candidate_path = Path(candidate)
            # For config directories, we only need read access, not write
            try:
                if candidate_path.exists() and candidate_path.is_dir():
                    return candidate_path
                # Try to create if it doesn't exist
                candidate_path.mkdir(parents=True, exist_ok=True)
                return candidate_path
            except (OSError, PermissionError):
                # If we can't create, continue to next candidate
                continue

    # Check if running in container with comprehensive detection
    container_indicators = [
        os.getenv("DOCKER_CONTAINER", "").lower() in ("1", "true", "yes", "on"),
        os.path.exists("/.dockerenv"),
        os.path.exists("/app"),
        os.getenv("container") is not None,  # systemd container detection
    ]

    if any(container_indicators):
        container_path = Path("/app/config")
        try:
            if container_path.exists() or container_path.parent.exists():
                container_path.mkdir(parents=True, exist_ok=True)
                return container_path
        except (OSError, PermissionError):
            # Container path failed, fall through to other options
            pass

    # Config directory fallbacks with preference for existing directories
    fallback_candidates = [
        Path("config"),  # Local project config (for development)
        Path.cwd() / "config",  # Current working directory config
        Path.home() / ".config" / "docker-mcp",  # User config directory
        Path.home() / ".docker-mcp" / "config",  # Alternative user config
        Path("/etc/docker-mcp"),  # System-wide config (read-only usually)
    ]

    # First pass: look for existing config directories
    for candidate_path in fallback_candidates:
        if candidate_path.exists() and candidate_path.is_dir():
            try:
                # Test if we can read from the directory
                list(candidate_path.iterdir())
                return candidate_path
            except (OSError, PermissionError):
                continue

    # Second pass: try to create config directories
    for candidate_path in fallback_candidates[:-1]:  # Skip system dir for creation
        try:
            candidate_path.mkdir(parents=True, exist_ok=True)
            return candidate_path
        except (OSError, PermissionError):
            continue

    # If all else fails, return the primary fallback even if not accessible
    # Let the calling code handle the permission error
    return Path("config")


class DockerMCPServer:
    """FastMCP server for Docker management via Docker contexts."""

    def __init__(self, config: DockerMCPConfig, config_path: str | None = None):
        self.config = config
        self._config_path: str = config_path or os.getenv("DOCKER_HOSTS_CONFIG") or str(get_config_dir() / "hosts.yml")


        # Use server logger (writes to mcp_server.log)
        self.logger = get_server_logger()

        # Initialize core managers
        self.context_manager = DockerContextManager(config)

        # Initialize port cache
        data_dir = get_data_dir()
        self.port_cache = PortCache(data_dir)

        # Initialize service layer
        self.host_service = HostService(config)
        self.container_service = ContainerService(config, self.context_manager, self.port_cache)
        self.stack_service = StackService(config, self.context_manager)
        self.config_service = ConfigService(config, self.context_manager)
        self.cleanup_service = CleanupService(config)
        self.schedule_service = ScheduleService(config)

        # Initialize remaining tools (logs not yet moved to services)
        self.log_tools = LogTools(config, self.context_manager)

        # Initialize hot reload manager (always enabled)
        self.hot_reload_manager = HotReloadManager()
        self.hot_reload_manager.setup_hot_reload(self._config_path, self)

        # FastMCP app will be created later to prevent auto-start
        self.app = None

        self.logger.info(
            "Docker MCP Server initialized",
            hosts=list(config.hosts.keys()),
            server_config=config.server.model_dump(),
            hot_reload_enabled=True,
            config_path=self._config_path,
        )

    def _initialize_app(self) -> None:
        """Initialize FastMCP app, middleware, and register tools."""
        # Create FastMCP server
        self.app = FastMCP("Docker Context Manager")

        # Add middleware in logical order (first added = first executed)
        # Error handling first to catch all errors
        self.app.add_middleware(ErrorHandlingMiddleware(
            include_traceback=os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG",
            track_error_stats=True
        ))

        # Rate limiting to protect against abuse
        rate_limit = float(os.getenv("RATE_LIMIT_PER_SECOND", "50.0"))
        self.app.add_middleware(RateLimitingMiddleware(
            max_requests_per_second=rate_limit,
            burst_capacity=int(rate_limit * 2),
            enable_global_limit=True
        ))

        # Timing middleware to monitor performance
        slow_threshold = float(os.getenv("SLOW_REQUEST_THRESHOLD_MS", "5000.0"))
        self.app.add_middleware(TimingMiddleware(
            slow_request_threshold_ms=slow_threshold,
            track_statistics=True
        ))

        # Logging middleware last to log everything (including middleware processing)
        self.app.add_middleware(LoggingMiddleware(
            include_payloads=os.getenv("LOG_INCLUDE_PAYLOADS", "true").lower() == "true",
            max_payload_length=int(os.getenv("LOG_MAX_PAYLOAD_LENGTH", "1000"))
        ))

        self.logger.info(
            "FastMCP middleware initialized",
            error_handling=True,
            rate_limiting=f"{rate_limit} req/sec",
            timing_monitoring=f"{slow_threshold}ms threshold",
            logging="dual output (console + files)"
        )

        # Register consolidated tools (3 tools replace 13 individual tools)
        self.app.tool(self.docker_hosts)      # Consolidates: add_docker_host, list_docker_hosts, list_host_ports, update_host_config, import_ssh_config
        self.app.tool(self.docker_container)  # Consolidates: list_containers, get_container_info, manage_container, get_container_logs
        self.app.tool(self.docker_compose)    # Consolidates: deploy_stack, manage_stack, list_stacks, discover_compose_paths + NEW: logs capability

        # Register MCP resources for data access (complement tools with clean URI-based data retrieval)
        self._register_resources()

    def _register_resources(self) -> None:
        """Register MCP resources for data access.
        
        Resources provide clean, URI-based access to data without side effects.
        They complement tools by offering cacheable, parametrized data retrieval.
        """
        try:
            # Port mapping resource - ports://{host_id}
            port_resource = PortMappingResource(
                container_service=self.container_service,
                server_instance=self
            )
            self.app.add_resource(port_resource)

            # Docker host info resource - docker://{host_id}/info
            info_resource = DockerInfoResource(
                context_manager=self.context_manager,
                host_service=self.host_service
            )
            self.app.add_resource(info_resource)

            # Docker containers resource - docker://{host_id}/containers
            containers_resource = DockerContainersResource(
                container_service=self.container_service
            )
            self.app.add_resource(containers_resource)

            # Docker compose resource - docker://{host_id}/compose
            compose_resource = DockerComposeResource(
                stack_service=self.stack_service
            )
            self.app.add_resource(compose_resource)

            self.logger.info(
                "MCP resources registered successfully",
                resources_count=4,
                uri_schemes=["ports://", "docker://"],
            )

        except Exception as e:
            self.logger.error(
                "Failed to register MCP resources",
                error=str(e)
            )
            # Don't fail the server startup, just log the error
            # Resources are optional enhancements to the tool-based API

    # Consolidated Tools Implementation

    async def docker_hosts(
        self,
        action: Annotated[Literal["list", "add", "ports", "compose_path", "import_ssh", "cleanup", "disk_usage", "schedule", "reserve_port", "release_port", "list_reservations"], Field(description="Action to perform")],
        host_id: Annotated[str, Field(default="", description="Host identifier", min_length=1)] = "",
        ssh_host: Annotated[str, Field(default="", description="SSH hostname or IP address", min_length=1)] = "",
        ssh_user: Annotated[str, Field(default="", description="SSH username", min_length=1)] = "",
        ssh_port: Annotated[int, Field(default=22, ge=1, le=65535, description="SSH port number")] = 22,
        ssh_key_path: Annotated[str, Field(default="", description="Path to SSH private key file")] = "",
        description: Annotated[str, Field(default="", description="Host description")] = "",
        tags: Annotated[list[str], Field(default_factory=list, description="Host tags")] = [],
        test_connection: Annotated[bool, Field(default=True, description="Test connection when adding host")] = True,
        include_stopped: Annotated[bool, Field(default=False, description="Include stopped containers in listings")] = False,
        compose_path: Annotated[str, Field(default="", description="Docker Compose file path")] = "",
        enabled: Annotated[bool, Field(default=True, description="Whether host is enabled")] = True,
        ssh_config_path: Annotated[str, Field(default="", description="Path to SSH config file")] = "",
        selected_hosts: Annotated[str, Field(default="", description="Comma-separated list of hosts to select")] = "",
        compose_path_overrides: Annotated[dict[str, str], Field(default_factory=dict, description="Per-host compose path overrides")] = {},
        auto_confirm: Annotated[bool, Field(default=False, description="Auto-confirm operations without prompting")] = False,
        cleanup_type: Annotated[str, Field(default="", description="Type of cleanup to perform")] = "",
        schedule_action: Annotated[str, Field(default="", description="Schedule action to perform")] = "",
        schedule_frequency: Annotated[str, Field(default="", description="Cleanup frequency")] = "",
        schedule_time: Annotated[str, Field(default="", pattern=r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$", description="Time to run cleanup in HH:MM format (24-hour)")] = "",
        schedule_id: Annotated[str, Field(default="", description="Schedule identifier for management")] = "",

        # Enhanced port action parameters
        export_format: Annotated[str, Field(default="", description="Export format for port data")] = "",
        filter_project: Annotated[str, Field(default="", description="Filter by compose project")] = "",
        filter_range: Annotated[str, Field(default="", description="Filter by port range (e.g., '8000-9000')")] = "",
        filter_protocol: Annotated[str, Field(default="", description="Filter by protocol")] = "",
        scan_available: Annotated[bool, Field(default=False, description="Scan for truly available ports")] = False,
        suggest_next: Annotated[bool, Field(default=False, description="Suggest next available port")] = False,
        use_cache: Annotated[bool, Field(default=True, description="Use cached data (default: True)")] = True,

        # Port reservation parameters
        port: Annotated[int, Field(default=0, ge=1, le=65535, description="Port number for reservation operations")] = 0,
        protocol: Annotated[Literal["TCP", "UDP"], Field(default="TCP", description="Protocol for port reservation")] = "TCP",
        service_name: Annotated[str, Field(default="", description="Service name for port reservation", min_length=1)] = "",
        reserved_by: Annotated[str, Field(default="user", description="Who is reserving the port")] = "user",
        expires_days: Annotated[int, Field(default=0, ge=0, description="Days until reservation expires (0 for permanent)")] = 0,
        notes: Annotated[str, Field(default="", description="Notes for the reservation")] = "",
    ) -> dict[str, Any]:
        """Consolidated Docker hosts management tool.
        
        Actions:
        - list: List all configured Docker hosts  
        - add: Add a new Docker host (requires: host_id, ssh_host, ssh_user; optional: ssh_port, ssh_key_path, description, tags, compose_path, enabled)
        - ports: List port mappings for a host (requires: host_id; supports filtering, export, availability scanning)
        - compose_path: Update host compose path (requires: host_id, compose_path)
        - import_ssh: Import hosts from SSH config
        - reserve_port: Reserve a port on a host (requires: host_id, port, service_name; optional: protocol, reserved_by, expires_days, notes)
        - release_port: Release a port reservation (requires: host_id, port; optional: protocol)
        - list_reservations: List port reservations for a host (requires: host_id)
        """
        # Handle default values for list parameters
        if tags is None:
            tags = []
        if compose_path_overrides is None:
            compose_path_overrides = {}

        # Validate action parameter
        valid_actions = ["list", "add", "ports", "compose_path", "import_ssh", "cleanup", "disk_usage", "schedule"]
        if action not in valid_actions:
            return {
                "success": False,
                "error": f"Invalid action '{action}'. Must be one of: {', '.join(valid_actions)}"
            }

        try:
            # Route to appropriate handler with validation
            if action == "list":
                return await self.list_docker_hosts()

            elif action == "add":
                # Validate required parameters for add action
                if not host_id:
                    return {"success": False, "error": "host_id is required for add action"}
                if not ssh_host:
                    return {"success": False, "error": "ssh_host is required for add action"}
                if not ssh_user:
                    return {"success": False, "error": "ssh_user is required for add action"}

                # Validate port range
                if not (1 <= ssh_port <= 65535):
                    return {"success": False, "error": f"ssh_port must be between 1 and 65535, got {ssh_port}"}

                return await self.add_docker_host(
                    host_id, ssh_host, ssh_user, ssh_port, ssh_key_path, description, tags, test_connection, compose_path, enabled
                )

            elif action == "ports":
                # Validate required parameters for ports action
                if not host_id:
                    return {"success": False, "error": "host_id is required for ports action"}

                # Validate export format
                if export_format and export_format not in ["json", "csv", "markdown"]:
                    return {"success": False, "error": "export_format must be one of: json, csv, markdown"}

                # Validate filter protocol
                if filter_protocol and filter_protocol.upper() not in ["TCP", "UDP"]:
                    return {"success": False, "error": "filter_protocol must be TCP or UDP"}

                # Validate port range format
                if filter_range:
                    try:
                        if '-' in filter_range:
                            start, end = map(int, filter_range.split('-'))
                            if not (1 <= start <= end <= 65535):
                                raise ValueError("Invalid range")
                        else:
                            port = int(filter_range)
                            if not (1 <= port <= 65535):
                                raise ValueError("Invalid port")
                    except ValueError:
                        return {"success": False, "error": "filter_range must be a port number or range like '8000-9000'"}

                result = await self.list_host_ports_enhanced(
                    host_id, include_stopped, export_format, filter_project,
                    filter_range, filter_protocol, scan_available, suggest_next, use_cache
                )
                return result

            elif action == "compose_path":
                # Validate required parameters for compose_path action
                if not host_id:
                    return {"success": False, "error": "host_id is required for compose_path action"}
                if not compose_path:
                    return {"success": False, "error": "compose_path is required for compose_path action"}

                result = await self.update_host_config(host_id, compose_path)
                # Convert ToolResult to dict for consistency
                if hasattr(result, 'content') and hasattr(result, 'structured_content'):
                    return result.structured_content or {"success": True, "data": str(result.content)}
                return result

            elif action == "import_ssh":
                result = await self.import_ssh_config(
                    ssh_config_path, selected_hosts, compose_path_overrides, auto_confirm
                )
                # Convert ToolResult to dict for consistency
                if hasattr(result, 'content') and hasattr(result, 'structured_content'):
                    return result.structured_content or {"success": True, "data": str(result.content)}
                return result

            elif action == "cleanup":
                # Validate required parameters for cleanup action
                if not host_id:
                    return {"success": False, "error": "host_id is required for cleanup action"}
                if not cleanup_type:
                    return {"success": False, "error": "cleanup_type is required for cleanup action"}
                if cleanup_type not in ["check", "safe", "moderate", "aggressive"]:
                    return {"success": False, "error": "cleanup_type must be one of: check, safe, moderate, aggressive"}

                return await self.cleanup_service.docker_cleanup(host_id, cleanup_type)

            elif action == "disk_usage":
                # Validate required parameters for disk_usage action
                if not host_id:
                    return {"success": False, "error": "host_id is required for disk_usage action"}

                return await self.cleanup_service.docker_disk_usage(host_id)

            elif action == "schedule":
                # Validate required parameters for schedule action
                if not schedule_action:
                    return {"success": False, "error": "schedule_action is required for schedule action"}
                if schedule_action not in ["add", "remove", "list", "enable", "disable"]:
                    return {"success": False, "error": "schedule_action must be one of: add, remove, list, enable, disable"}

                return await self.schedule_service.handle_schedule_action(
                    schedule_action, host_id, cleanup_type,
                    schedule_frequency, schedule_time, schedule_id
                )

            elif action == "reserve_port":
                # Validate required parameters for reserve_port action
                if not host_id:
                    return {"success": False, "error": "host_id is required for reserve_port action"}
                if port <= 0:
                    return {"success": False, "error": "port is required for reserve_port action"}
                if not service_name:
                    return {"success": False, "error": "service_name is required for reserve_port action"}

                # Validate protocol
                if protocol.upper() not in ["TCP", "UDP"]:
                    return {"success": False, "error": "protocol must be TCP or UDP"}

                return await self.reserve_port(host_id, port, protocol.upper(), service_name, reserved_by, expires_days, notes)

            elif action == "release_port":
                # Validate required parameters for release_port action
                if not host_id:
                    return {"success": False, "error": "host_id is required for release_port action"}
                if port <= 0:
                    return {"success": False, "error": "port is required for release_port action"}

                # Validate protocol
                if protocol.upper() not in ["TCP", "UDP"]:
                    return {"success": False, "error": "protocol must be TCP or UDP"}

                return await self.release_port(host_id, port, protocol.upper())

            elif action == "list_reservations":
                # Validate required parameters for list_reservations action
                if not host_id:
                    return {"success": False, "error": "host_id is required for list_reservations action"}

                return await self.list_port_reservations(host_id)

        except Exception as e:
            self.logger.error("docker_hosts tool error", action=action, error=str(e))
            return {
                "success": False,
                "error": f"Tool execution failed: {str(e)}",
                "action": action
            }

    async def docker_container(
        self,
        action: Annotated[Literal["list", "info", "start", "stop", "restart", "build", "logs"], Field(description="Action to perform")],
        host_id: Annotated[str, Field(default="", description="Host identifier", min_length=1)] = "",
        container_id: Annotated[str, Field(default="", description="Container identifier", min_length=1)] = "",
        all_containers: Annotated[bool, Field(default=False, description="Include all containers, not just running")] = False,
        limit: Annotated[int, Field(default=20, ge=1, le=1000, description="Maximum number of results to return")] = 20,
        offset: Annotated[int, Field(default=0, ge=0, description="Number of results to skip")] = 0,
        follow: Annotated[bool, Field(default=False, description="Follow log output")] = False,
        lines: Annotated[int, Field(default=100, ge=1, le=10000, description="Number of log lines to retrieve")] = 100,
        force: Annotated[bool, Field(default=False, description="Force the operation")] = False,
        timeout: Annotated[int, Field(default=10, ge=1, le=300, description="Operation timeout in seconds")] = 10
    ) -> dict[str, Any]:
        """Consolidated Docker container management tool.
        
        Actions:
        - list: List containers on a host (requires: host_id)
        - info: Get container information (requires: host_id, container_id)
        - start/stop/restart/build: Manage container (requires: host_id, container_id)
        - logs: Get container logs (requires: host_id, container_id)
        - pull: Pull container image (requires: host_id, container_id or image name)
        """
        # Validate action parameter
        valid_actions = ["list", "info", "start", "stop", "restart", "build", "logs", "pull"]
        if action not in valid_actions:
            return {
                "success": False,
                "error": f"Invalid action '{action}'. Must be one of: {', '.join(valid_actions)}"
            }

        try:
            # Route to appropriate handler with validation
            if action == "list":
                # Validate required parameters for list action
                if not host_id:
                    return {"success": False, "error": "host_id is required for list action"}

                # Validate pagination parameters
                if limit < 1 or limit > 1000:
                    return {"success": False, "error": "limit must be between 1 and 1000"}
                if offset < 0:
                    return {"success": False, "error": "offset must be >= 0"}

                result = await self.list_containers(host_id, all_containers, limit, offset)
                # Convert ToolResult to dict for consistency
                if hasattr(result, 'structured_content'):
                    return result.structured_content or {"success": True, "data": "No structured content"}
                return result

            elif action == "info":
                # Validate required parameters for info action
                if not host_id:
                    return {"success": False, "error": "host_id is required for info action"}
                if not container_id:
                    return {"success": False, "error": "container_id is required for info action"}

                result = await self.get_container_info(host_id, container_id)
                # Convert ToolResult to dict for consistency
                if hasattr(result, 'structured_content'):
                    return result.structured_content or {"success": True, "data": "No structured content"}
                return result

            elif action in ["start", "stop", "restart", "build"]:
                # Validate required parameters for container management actions
                if not host_id:
                    return {"success": False, "error": f"host_id is required for {action} action"}
                if not container_id:
                    return {"success": False, "error": f"container_id is required for {action} action"}

                # Validate timeout parameter
                if timeout < 1 or timeout > 300:
                    return {"success": False, "error": "timeout must be between 1 and 300 seconds"}

                result = await self.manage_container(host_id, container_id, action, force, timeout)
                # Convert ToolResult to dict for consistency
                if hasattr(result, 'structured_content'):
                    return result.structured_content or {"success": True, "data": "No structured content"}
                return result

            elif action == "logs":
                # Validate required parameters for logs action
                if not host_id:
                    return {"success": False, "error": "host_id is required for logs action"}
                if not container_id:
                    return {"success": False, "error": "container_id is required for logs action"}

                # Validate lines parameter
                if lines < 1 or lines > 1000:
                    return {"success": False, "error": "lines must be between 1 and 10000"}

                return await self.get_container_logs(host_id, container_id, lines, follow)

            elif action == "pull":
                # Validate required parameters for pull action
                if not host_id:
                    return {"success": False, "error": "host_id is required for pull action"}
                if not container_id:
                    return {"success": False, "error": "container_id is required for pull action (image name)"}

                # For pull, container_id is actually the image name
                result = await self.pull_image(host_id, container_id)
                # Convert ToolResult to dict for consistency
                if hasattr(result, 'structured_content'):
                    return result.structured_content or {"success": True, "data": "No structured content"}
                return result

        except Exception as e:
            self.logger.error("docker_container tool error", action=action, host_id=host_id, container_id=container_id, error=str(e))
            return {
                "success": False,
                "error": f"Tool execution failed: {str(e)}",
                "action": action,
                "host_id": host_id,
                "container_id": container_id
            }

    async def docker_compose(
        self,
        action: Annotated[Literal["list", "deploy", "up", "down", "restart", "build", "discover", "logs", "migrate"], Field(description="Action to perform")],
        host_id: Annotated[str, Field(default="", description="Host identifier", min_length=1)] = "",
        stack_name: Annotated[str, Field(default="", description="Stack name", min_length=1)] = "",
        compose_content: Annotated[str, Field(default="", description="Docker Compose file content")] = "",
        environment: Annotated[dict[str, str], Field(default_factory=dict, description="Environment variables")] = {},
        pull_images: Annotated[bool, Field(default=True, description="Pull images before deploying")] = True,
        recreate: Annotated[bool, Field(default=False, description="Recreate containers")] = False,
        follow: Annotated[bool, Field(default=False, description="Follow log output")] = False,
        lines: Annotated[int, Field(default=100, ge=1, le=10000, description="Number of log lines to retrieve")] = 100,
        dry_run: Annotated[bool, Field(default=False, description="Perform a dry run without making changes")] = False,
        options: Annotated[dict[str, str], Field(default_factory=dict, description="Additional options for the operation")] = {},
        target_host_id: Annotated[str, Field(default="", description="Target host ID for migration", min_length=1)] = "",
        remove_source: Annotated[bool, Field(default=False, description="Remove source stack after migration")] = False,
        skip_stop_source: Annotated[bool, Field(default=False, description="Skip stopping source stack before migration")] = False,
        start_target: Annotated[bool, Field(default=True, description="Start target stack after migration")] = True
    ) -> dict[str, Any]:
        """Consolidated Docker Compose stack management tool.
        
        Actions:
        - list: List stacks on a host (requires: host_id)
        - deploy: Deploy a stack (requires: host_id, stack_name, compose_content)
        - up/down/restart/build: Manage stack lifecycle (requires: host_id, stack_name)
        - discover: Discover compose paths on a host (requires: host_id)
        - logs: Get stack logs (requires: host_id, stack_name)
        - migrate: Migrate stack between hosts (requires: host_id, target_host_id, stack_name)
        """
        # Handle default values for dict parameters
        if environment is None:
            environment = {}
        if options is None:
            options = {}

        # Validate action parameter
        valid_actions = ["list", "deploy", "up", "down", "restart", "build", "pull", "discover", "logs", "migrate"]
        if action not in valid_actions:
            return {
                "success": False,
                "error": f"Invalid action '{action}'. Must be one of: {', '.join(valid_actions)}"
            }

        try:
            # Route to appropriate handler with validation
            if action == "list":
                # Validate required parameters for list action
                if not host_id:
                    return {"success": False, "error": "host_id is required for list action"}

                result = await self.list_stacks(host_id)
                # Convert ToolResult to dict for consistency
                if hasattr(result, 'structured_content'):
                    return result.structured_content or {"success": True, "data": "No structured content"}
                return result

            elif action == "deploy":
                # Validate required parameters for deploy action
                if not host_id:
                    return {"success": False, "error": "host_id is required for deploy action"}
                if not stack_name:
                    return {"success": False, "error": "stack_name is required for deploy action"}
                if not compose_content:
                    return {"success": False, "error": "compose_content is required for deploy action"}

                # Validate stack name format (basic validation)
                if not stack_name.replace("-", "").replace("_", "").isalnum():
                    return {"success": False, "error": "stack_name must contain only alphanumeric characters, hyphens, and underscores"}

                result = await self.deploy_stack(
                    host_id, stack_name, compose_content, environment, pull_images, recreate
                )
                # Convert ToolResult to dict for consistency
                if hasattr(result, 'structured_content'):
                    return result.structured_content or {"success": True, "data": "No structured content"}
                return result

            elif action in ["up", "down", "restart", "build", "pull"]:
                # Validate required parameters for stack management actions
                if not host_id:
                    return {"success": False, "error": f"host_id is required for {action} action"}
                if not stack_name:
                    return {"success": False, "error": f"stack_name is required for {action} action"}

                result = await self.manage_stack(host_id, stack_name, action, options)
                # Convert ToolResult to dict for consistency
                if hasattr(result, 'structured_content'):
                    return result.structured_content or {"success": True, "data": "No structured content"}
                return result

            elif action == "discover":
                # Validate required parameters for discover action
                if not host_id:
                    return {"success": False, "error": "host_id is required for discover action"}

                result = await self.discover_compose_paths(host_id)
                # Convert ToolResult to dict for consistency
                if hasattr(result, 'structured_content'):
                    return result.structured_content or {"success": True, "data": "No structured content"}
                return result

            elif action == "logs":
                # NEW CAPABILITY: Stack logs
                # Validate required parameters for logs action
                if not host_id:
                    return {"success": False, "error": "host_id is required for logs action"}
                if not stack_name:
                    return {"success": False, "error": "stack_name is required for logs action"}

                # Validate lines parameter
                if lines < 1 or lines > 1000:
                    return {"success": False, "error": "lines must be between 1 and 10000"}

                # Implement stack logs using docker-compose logs command
                try:
                    if host_id not in self.config.hosts:
                        return {"success": False, "error": f"Host {host_id} not found"}

                    # Use stack service to execute docker-compose logs command
                    logs_options = {
                        "tail": str(lines),
                        "follow": follow
                    }

                    result = await self.stack_service.manage_stack(host_id, stack_name, "logs", logs_options)

                    # Format the result for logs
                    if hasattr(result, 'structured_content') and result.structured_content:
                        logs_data = result.structured_content
                        # Extract logs from the result
                        if "output" in logs_data:
                            logs_lines = logs_data["output"].split('\n') if logs_data["output"] else []
                            return {
                                "success": True,
                                "host_id": host_id,
                                "stack_name": stack_name,
                                "logs": logs_lines,
                                "lines_requested": lines,
                                "lines_returned": len(logs_lines),
                                "follow": follow
                            }
                        else:
                            return logs_data
                    else:
                        return {"success": False, "error": "Failed to retrieve stack logs"}

                except Exception as e:
                    self.logger.error("docker_compose logs error", host_id=host_id, stack_name=stack_name, error=str(e))
                    return {"success": False, "error": f"Failed to get stack logs: {str(e)}"}

            elif action == "migrate":
                # Validate required parameters for migrate action
                if not host_id:
                    return {"success": False, "error": "host_id (source) is required for migrate action"}
                if not target_host_id:
                    return {"success": False, "error": "target_host_id is required for migrate action"}
                if not stack_name:
                    return {"success": False, "error": "stack_name is required for migrate action"}

                # Call the migration service
                result = await self.stack_service.migrate_stack(
                    source_host_id=host_id,
                    target_host_id=target_host_id,
                    stack_name=stack_name,
                    skip_stop_source=skip_stop_source,
                    start_target=start_target,
                    remove_source=remove_source,
                    dry_run=dry_run
                )

                # Convert ToolResult to dict for consistency
                if hasattr(result, 'structured_content'):
                    return result.structured_content or {"success": True, "data": "Migration completed"}
                return result

        except Exception as e:
            self.logger.error("docker_compose tool error", action=action, host_id=host_id, stack_name=stack_name, error=str(e))
            return {
                "success": False,
                "error": f"Tool execution failed: {str(e)}",
                "action": action,
                "host_id": host_id,
                "stack_name": stack_name
            }

    async def add_docker_host(
        self,
        host_id: str,
        ssh_host: str,
        ssh_user: str,
        ssh_port: int = 22,
        ssh_key_path: str | None = None,
        description: str = "",
        tags: list[str] | None = None,
        test_connection: bool = True,
        compose_path: str | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Add a new Docker host for management."""
        return await self.host_service.add_docker_host(
            host_id, ssh_host, ssh_user, ssh_port, ssh_key_path, description, tags, test_connection, compose_path, enabled
        )

    async def list_docker_hosts(self) -> dict[str, Any]:
        """List all configured Docker hosts."""
        return await self.host_service.list_docker_hosts()

    async def list_containers(
        self, host_id: str, all_containers: bool = False, limit: int = 20, offset: int = 0
    ) -> ToolResult:
        """List containers on a specific Docker host with pagination."""
        return await self.container_service.list_containers(host_id, all_containers, limit, offset)

    async def get_container_info(self, host_id: str, container_id: str) -> ToolResult:
        """Get detailed information about a specific container."""
        return await self.container_service.get_container_info(host_id, container_id)

    async def get_container_logs(
        self, host_id: str, container_id: str, lines: int = 100, follow: bool = False
    ) -> dict[str, Any]:
        """Get logs from a container.

        Args:
            host_id: Target Docker host identifier
            container_id: Container ID or name
            lines: Number of log lines to retrieve
            follow: Stream logs in real-time

        Returns:
            Container logs
        """
        try:
            if host_id not in self.config.hosts:
                return {"success": False, "error": f"Host {host_id} not found"}

            # Use log tools to get logs
            logs_result = await self.log_tools.get_container_logs(
                host_id=host_id,
                container_id=container_id,
                lines=lines,
                since=None,
                timestamps=False
            )

            # Extract logs array from ContainerLogs model for cleaner API
            if isinstance(logs_result, dict) and "logs" in logs_result:
                logs = logs_result["logs"]  # This is the list[str] of actual log lines
                truncated = logs_result.get("truncated", False)
            else:
                logs = []
                truncated = False

            return {
                "success": True,
                "host_id": host_id,
                "container_id": container_id,
                "logs": logs,  # Now this is list[str] of actual log lines
                "lines_requested": lines,
                "lines_returned": len(logs),
                "truncated": truncated,
                "follow": follow,
            }

        except Exception as e:
            self.logger.error(
                "Failed to get container logs",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {
                "success": False,
                "error": str(e),
                "host_id": host_id,
                "container_id": container_id,
            }

    async def manage_container(
        self, host_id: str, container_id: str, action: str, force: bool = False, timeout: int = 10
    ) -> ToolResult:
        """Unified container action management."""
        return await self.container_service.manage_container(host_id, container_id, action, force, timeout)

    async def pull_image(self, host_id: str, image_name: str) -> ToolResult:
        """Pull a Docker image on a remote host."""
        return await self.container_service.pull_image(host_id, image_name)

    async def list_host_ports(self, host_id: str, include_stopped: bool = False) -> ToolResult:
        """List all ports currently in use by containers on a Docker host."""
        return await self.container_service.list_host_ports(host_id, include_stopped)

    async def list_host_ports_enhanced(
        self,
        host_id: str,
        include_stopped: bool = False,
        export_format: str | None = None,
        filter_project: str | None = None,
        filter_range: str | None = None,
        filter_protocol: str | None = None,
        scan_available: bool = False,
        suggest_next: bool = False,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Enhanced port listing with filtering, scanning, and export capabilities."""
        try:
            # Get base port information
            result = await self.container_service.list_host_ports(host_id, include_stopped, use_cache)

            # Convert ToolResult to dict if needed
            if hasattr(result, 'structured_content'):
                port_data = result.structured_content
            else:
                port_data = result

            if not port_data.get("success", False):
                return port_data

            # Apply filters if specified
            port_mappings = port_data.get("port_mappings", [])
            if filter_project or filter_range or filter_protocol:
                port_mappings = await self._apply_port_filters(
                    port_mappings, filter_project, filter_range, filter_protocol
                )

            # Scan for available ports if requested
            available_ports = []
            suggested_port = None
            if scan_available or suggest_next:
                available_ports, suggested_port = await self._scan_available_ports(
                    host_id, filter_range, filter_protocol, suggest_next
                )

            # Update counts after filtering
            port_data["port_mappings"] = port_mappings
            port_data["total_ports"] = len(port_mappings)

            # Add enhanced data
            if scan_available:
                port_data["available_ports"] = available_ports
                port_data["available_count"] = len(available_ports)

            if suggest_next and suggested_port:
                port_data["suggested_next_port"] = suggested_port

            # Handle export format
            if export_format:
                export_data = await self._export_port_data(port_data, export_format)
                port_data["export_data"] = export_data
                port_data["export_format"] = export_format

            # Add enhanced metadata
            port_data["filters_applied"] = {
                "project": filter_project,
                "range": filter_range,
                "protocol": filter_protocol,
            }
            port_data["enhancements"] = {
                "scanned_available": scan_available,
                "suggested_next": suggest_next and suggested_port is not None,
                "exported": export_format is not None,
            }

            return port_data

        except Exception as e:
            self.logger.error("Failed to list enhanced host ports", host_id=host_id, error=str(e))
            return {
                "success": False,
                "error": f"Enhanced port listing failed: {str(e)}",
                "host_id": host_id,
            }

    async def _apply_port_filters(
        self,
        port_mappings: list[dict[str, Any]],
        filter_project: str | None,
        filter_range: str | None,
        filter_protocol: str | None
    ) -> list[dict[str, Any]]:
        """Apply filtering criteria to port mappings."""
        filtered_mappings = []

        for mapping in port_mappings:
            # Filter by compose project
            if filter_project:
                compose_project = mapping.get("compose_project", "")
                if not compose_project or filter_project.lower() not in compose_project.lower():
                    continue

            # Filter by protocol
            if filter_protocol:
                if mapping.get("protocol", "").upper() != filter_protocol.upper():
                    continue

            # Filter by port range
            if filter_range:
                try:
                    host_port = int(mapping.get("host_port", 0))
                    if '-' in filter_range:
                        start, end = map(int, filter_range.split('-'))
                        if not (start <= host_port <= end):
                            continue
                    else:
                        if host_port != int(filter_range):
                            continue
                except (ValueError, TypeError):
                    continue

            filtered_mappings.append(mapping)

        return filtered_mappings

    async def _scan_available_ports(
        self,
        host_id: str,
        filter_range: str | None,
        filter_protocol: str | None,
        suggest_next: bool
    ) -> tuple[list[int], int | None]:
        """Scan for available ports on the host."""
        try:
            # Default range if not specified
            if filter_range:
                if '-' in filter_range:
                    start, end = map(int, filter_range.split('-'))
                else:
                    port = int(filter_range)
                    start, end = port, port + 100  # Scan 100 ports from the specified port
            else:
                start, end = 8000, 9000  # Default range for user applications

            protocol = filter_protocol.upper() if filter_protocol else "TCP"
            range_str = f"{start}-{end}"

            # Check cache first
            if self.port_cache:
                cached_ports = await self.port_cache.get_available_ports(host_id, range_str, protocol)
                if cached_ports is not None:
                    suggested_port = cached_ports[0] if suggest_next and cached_ports else None
                    return cached_ports, suggested_port

            # Get currently used ports from Docker
            result = await self.container_service.list_host_ports(host_id, include_stopped=True, use_cache=True)

            if hasattr(result, 'structured_content'):
                port_data = result.structured_content
            else:
                port_data = result

            if not port_data.get("success", False):
                return [], None

            # Extract used ports for the protocol
            used_ports = set()
            for mapping in port_data.get("port_mappings", []):
                if mapping.get("protocol", "").upper() == protocol:
                    try:
                        used_ports.add(int(mapping.get("host_port", 0)))
                    except (ValueError, TypeError):
                        continue

            # Find available ports in range
            available_ports = []
            for port in range(start, end + 1):
                if port not in used_ports:
                    # Basic check if port is available (not a comprehensive system-wide check)
                    available_ports.append(port)

            # Cache the results
            if self.port_cache:
                await self.port_cache.set_available_ports(host_id, range_str, available_ports, protocol, ttl_minutes=15)

            # Suggest next available port
            suggested_port = available_ports[0] if suggest_next and available_ports else None

            return available_ports, suggested_port

        except Exception as e:
            self.logger.warning("Failed to scan available ports", host_id=host_id, error=str(e))
            return [], None

    async def _export_port_data(self, port_data: dict[str, Any], export_format: str) -> str:
        """Export port data in the specified format."""
        try:
            if export_format == "json":
                import json
                return json.dumps(port_data, indent=2)

            elif export_format == "csv":
                import csv
                import io

                output = io.StringIO()
                writer = csv.writer(output)

                # Headers
                writer.writerow([
                    "Host Port", "Container Port", "Protocol", "Container Name",
                    "Container ID", "Image", "Compose Project", "Conflicts"
                ])

                # Data rows
                for mapping in port_data.get("port_mappings", []):
                    writer.writerow([
                        mapping.get("host_port", ""),
                        mapping.get("container_port", ""),
                        mapping.get("protocol", ""),
                        mapping.get("container_name", ""),
                        mapping.get("container_id", ""),
                        mapping.get("image", ""),
                        mapping.get("compose_project", ""),
                        "Yes" if mapping.get("is_conflict", False) else "No"
                    ])

                return output.getvalue()

            elif export_format == "markdown":
                lines = [
                    f"# Port Mappings for {port_data.get('host_id', 'Unknown Host')}",
                    "",
                    f"**Total Ports:** {port_data.get('total_ports', 0)}",
                    f"**Total Containers:** {port_data.get('total_containers', 0)}",
                    f"**Conflicts:** {len(port_data.get('conflicts', []))}",
                    "",
                    "| Host Port | Container Port | Protocol | Container | Image | Project | Conflicts |",
                    "|-----------|----------------|----------|-----------|-------|---------|-----------|"
                ]

                for mapping in port_data.get("port_mappings", []):
                    lines.append(
                        f"| {mapping.get('host_port', '')} | {mapping.get('container_port', '')} | "
                        f"{mapping.get('protocol', '')} | {mapping.get('container_name', '')} | "
                        f"{mapping.get('image', '')} | {mapping.get('compose_project', '')} | "
                        f"{'⚠️' if mapping.get('is_conflict', False) else '✅'} |"
                    )

                if port_data.get("available_ports"):
                    lines.extend([
                        "",
                        "## Available Ports",
                        f"Found {len(port_data['available_ports'])} available ports in scanned range.",
                        ""
                    ])

                    if port_data.get("suggested_next_port"):
                        lines.append(f"**Suggested next port:** {port_data['suggested_next_port']}")

                return "\n".join(lines)

            else:
                return f"Unsupported export format: {export_format}"

        except Exception as e:
            self.logger.error("Failed to export port data", export_format=export_format, error=str(e))
            return f"Export failed: {str(e)}"

    async def reserve_port(
        self,
        host_id: str,
        port: int,
        protocol: str,
        service_name: str,
        reserved_by: str,
        expires_days: int | None,
        notes: str
    ) -> dict[str, Any]:
        """Reserve a port on a host."""
        try:
            # Validate host exists
            if host_id not in self.config.hosts:
                return {"success": False, "error": f"Host '{host_id}' not found"}

            # Initialize cache if needed
            if self.port_cache:
                await self.port_cache.initialize()
            else:
                return {"success": False, "error": "Port cache not available"}

            # Create reservation object
            from datetime import datetime, timedelta

            from .core.cache import PortReservation

            reserved_at = datetime.now().isoformat()
            expires_at = None
            if expires_days:
                expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()

            reservation = PortReservation(
                host_id=host_id,
                port=port,
                protocol=protocol,
                service_name=service_name,
                reserved_by=reserved_by,
                reserved_at=reserved_at,
                expires_at=expires_at,
                notes=notes
            )

            # Try to reserve the port
            success = await self.port_cache.reserve_port(reservation)

            if success:
                return {
                    "success": True,
                    "message": f"Port {port}/{protocol} reserved for {service_name}",
                    "reservation": reservation.model_dump(),
                    "host_id": host_id,
                }
            else:
                return {
                    "success": False,
                    "error": f"Port {port}/{protocol} is already reserved on {host_id}",
                    "host_id": host_id,
                }

        except Exception as e:
            self.logger.error("Failed to reserve port", host_id=host_id, port=port, error=str(e))
            return {
                "success": False,
                "error": f"Port reservation failed: {str(e)}",
                "host_id": host_id,
            }

    async def release_port(
        self,
        host_id: str,
        port: int,
        protocol: str
    ) -> dict[str, Any]:
        """Release a port reservation."""
        try:
            # Validate host exists
            if host_id not in self.config.hosts:
                return {"success": False, "error": f"Host '{host_id}' not found"}

            # Initialize cache if needed
            if self.port_cache:
                await self.port_cache.initialize()
            else:
                return {"success": False, "error": "Port cache not available"}

            # Try to release the port
            success = await self.port_cache.release_port(host_id, port, protocol)

            if success:
                return {
                    "success": True,
                    "message": f"Port {port}/{protocol} reservation released",
                    "host_id": host_id,
                    "port": port,
                    "protocol": protocol,
                }
            else:
                return {
                    "success": False,
                    "error": f"No reservation found for port {port}/{protocol} on {host_id}",
                    "host_id": host_id,
                }

        except Exception as e:
            self.logger.error("Failed to release port", host_id=host_id, port=port, error=str(e))
            return {
                "success": False,
                "error": f"Port release failed: {str(e)}",
                "host_id": host_id,
            }

    async def list_port_reservations(self, host_id: str) -> dict[str, Any]:
        """List all port reservations for a host."""
        try:
            # Validate host exists
            if host_id not in self.config.hosts:
                return {"success": False, "error": f"Host '{host_id}' not found"}

            # Initialize cache if needed
            if self.port_cache:
                await self.port_cache.initialize()
            else:
                return {"success": False, "error": "Port cache not available"}

            # Get reservations
            reservations = await self.port_cache.get_reservations(host_id)

            # Separate active and expired reservations
            from datetime import datetime
            now = datetime.now().isoformat()

            active_reservations = []
            expired_reservations = []

            for reservation in reservations:
                if reservation.expires_at and reservation.expires_at < now:
                    expired_reservations.append(reservation.model_dump())
                else:
                    active_reservations.append(reservation.model_dump())

            return {
                "success": True,
                "host_id": host_id,
                "total_reservations": len(reservations),
                "active_reservations": active_reservations,
                "expired_reservations": expired_reservations,
                "summary": {
                    "total": len(reservations),
                    "active": len(active_reservations),
                    "expired": len(expired_reservations),
                    "protocols": {
                        "TCP": len([r for r in reservations if r.protocol == "TCP"]),
                        "UDP": len([r for r in reservations if r.protocol == "UDP"]),
                    }
                }
            }

        except Exception as e:
            self.logger.error("Failed to list port reservations", host_id=host_id, error=str(e))
            return {
                "success": False,
                "error": f"Listing reservations failed: {str(e)}",
                "host_id": host_id,
            }

    async def deploy_stack(
        self,
        host_id: str,
        stack_name: str,
        compose_content: str,
        environment: dict[str, str] | None = None,
        pull_images: bool = True,
        recreate: bool = False,
    ) -> ToolResult:
        """Deploy a Docker Compose stack to a remote host."""
        return await self.stack_service.deploy_stack(
            host_id, stack_name, compose_content, environment, pull_images, recreate
        )

    async def manage_stack(
        self, host_id: str, stack_name: str, action: str, options: dict[str, Any] | None = None
    ) -> ToolResult:
        """Unified stack lifecycle management."""
        return await self.stack_service.manage_stack(host_id, stack_name, action, options)

    async def list_stacks(self, host_id: str) -> ToolResult:
        """List Docker Compose stacks on a host."""
        return await self.stack_service.list_stacks(host_id)

    async def update_host_config(self, host_id: str, compose_path: str) -> ToolResult:
        """Update host configuration with compose file path."""
        return await self.config_service.update_host_config(host_id, compose_path)

    async def discover_compose_paths(self, host_id: str | None = None) -> ToolResult:
        """Discover Docker Compose file locations and guide user through configuration."""
        return await self.config_service.discover_compose_paths(host_id)

    async def import_ssh_config(
        self,
        ssh_config_path: str | None = None,
        selected_hosts: str | None = None,
        compose_path_overrides: dict[str, str] | None = None,
        auto_confirm: bool = False,
    ) -> ToolResult:
        """Import hosts from SSH config with interactive selection and compose path discovery."""
        return await self.config_service.import_ssh_config(
            ssh_config_path, selected_hosts, compose_path_overrides, auto_confirm, self._config_path
        )

    def _to_dict(self, result: Any, fallback_msg: str = "No structured content") -> dict[str, Any]:
        """Convert ToolResult to dictionary for programmatic access."""
        if hasattr(result, "structured_content"):
            return result.structured_content or {"success": True, "data": fallback_msg}
        return result

    def update_configuration(self, new_config: DockerMCPConfig) -> None:
        """Update server configuration and reinitialize components."""
        self.config = new_config

        # Update managers with new config
        self.context_manager.config = new_config

        # Update service classes with new config
        self.host_service.config = new_config
        self.container_service.config = new_config
        self.stack_service.config = new_config
        self.config_service.config = new_config
        self.cleanup_service.config = new_config
        self.schedule_service.config = new_config

        # Update remaining tools with new config
        self.log_tools.config = new_config

        self.logger.info("Configuration updated", hosts=list(new_config.hosts.keys()))

    async def start_hot_reload(self) -> None:
        """Start hot reload watcher if configured."""
        await self.hot_reload_manager.start_hot_reload()

    async def stop_hot_reload(self) -> None:
        """Stop hot reload watcher."""
        await self.hot_reload_manager.stop_hot_reload()

    def run(self) -> None:
        """Run the FastMCP server."""
        try:
            # Initialize FastMCP app (delayed to prevent auto-start)
            self._initialize_app()

            self.logger.info(
                "Starting Docker MCP Server",
                host=self.config.server.host,
                port=self.config.server.port,
            )

            # FastMCP.run() is synchronous and handles its own event loop
            self.app.run(
                transport="streamable-http",
                host=self.config.server.host,
                port=self.config.server.port,
            )

        except Exception as e:
            self.logger.error("Server startup failed", error=str(e))
            raise


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    from dotenv import load_dotenv

    load_dotenv()

    default_host = os.getenv("FASTMCP_HOST", "127.0.0.1")  # nosec B104 - Use 0.0.0.0 for container deployment
    default_port = int(os.getenv("FASTMCP_PORT", "8000"))
    default_log_level = os.getenv("LOG_LEVEL", "INFO")
    default_config = os.getenv("DOCKER_HOSTS_CONFIG", str(get_config_dir() / "hosts.yml"))

    parser = argparse.ArgumentParser(description="FastMCP Docker SSH Manager")
    parser.add_argument("--host", default=default_host, help="Server host")
    parser.add_argument("--port", type=int, default=default_port, help="Server port")
    parser.add_argument("--config", default=default_config, help="Configuration file path")
    parser.add_argument(
        "--log-level",
        default=default_log_level,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    parser.add_argument(
        "--validate-config", action="store_true", help="Validate configuration and exit"
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Setup unified logging (console + files) with enhanced configuration
    from docker_mcp.core.logging_config import get_server_logger, setup_logging

    # Determine log directory with fallback options
    log_dir_candidates = [
        os.getenv("LOG_DIR"),  # Explicit environment override
        str(get_data_dir() / "logs"),  # Primary data directory
        str(Path.home() / ".local" / "share" / "docker-mcp" / "logs"),  # User fallback
        "/tmp/docker-mcp-logs"  # System fallback
    ]

    log_dir = None
    for candidate in log_dir_candidates:
        if candidate:
            try:
                candidate_path = Path(candidate)
                candidate_path.mkdir(parents=True, exist_ok=True)
                if candidate_path.is_dir() and os.access(candidate_path, os.W_OK):
                    log_dir = str(candidate_path)
                    break
            except (OSError, PermissionError):
                continue

    if not log_dir:
        print("Warning: Unable to create log directory, using console-only logging")
        log_dir = None

    # Parse log file size with validation
    try:
        max_file_size_mb = int(os.getenv("LOG_FILE_SIZE_MB", "10"))
        if max_file_size_mb < 1 or max_file_size_mb > 100:
            max_file_size_mb = 10  # Reset to default if out of range
    except ValueError:
        max_file_size_mb = 10

    # Setup logging with error handling
    try:
        setup_logging(
            log_dir=log_dir,
            log_level=args.log_level,
            max_file_size_mb=max_file_size_mb
        )
        logger = get_server_logger()

        # Log successful initialization with configuration details
        logger.info(
            "Logging system initialized",
            log_dir=log_dir,
            log_level=args.log_level,
            max_file_size_mb=max_file_size_mb,
            console_logging=True,
            file_logging=log_dir is not None
        )
    except Exception as e:
        print(f"Warning: Logging setup failed ({e}), using basic console logging")
        import logging
        logging.basicConfig(
            level=getattr(logging, args.log_level.upper(), logging.INFO),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        logger = logging.getLogger("docker_mcp")

    try:
        # Load configuration
        config = load_config(args.config)

        # Override server config from CLI args
        config.server.host = args.host
        config.server.port = args.port
        config.server.log_level = args.log_level

        # Validate configuration only
        if args.validate_config:
            logger.info("Configuration validation successful")
            print("✅ Configuration is valid")
            return

        # Create and run server (hot reload always enabled)
        config_path_for_reload = args.config or os.getenv("DOCKER_HOSTS_CONFIG", str(get_config_dir() / "hosts.yml"))

        logger.info(
            "Hot reload configuration",
            config_path=config_path_for_reload,
            args_config=args.config,
            env_config=os.getenv("DOCKER_HOSTS_CONFIG"),
        )

        server = DockerMCPServer(config, config_path=config_path_for_reload)

        # Start hot reload in background (always enabled)
        async def start_hot_reload():
            await server.start_hot_reload()

        # Run hot reload starter in the background
        import asyncio
        import threading

        def run_hot_reload():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(start_hot_reload())
            # Keep the loop running to handle file changes
            loop.run_forever()

        hot_reload_thread = threading.Thread(target=run_hot_reload, daemon=True)
        hot_reload_thread.start()
        logger.info("Hot reload enabled for configuration changes")

        server.run()

    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    except Exception as e:
        logger.error("Server error", error=str(e))
        sys.exit(1)


# Note: FastMCP dev mode not used - we run our own server with hot reload

if __name__ == "__main__":
    main()
