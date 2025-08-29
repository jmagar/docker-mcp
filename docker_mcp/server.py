"""
FastMCP Docker SSH Manager Server

A production-ready FastMCP server for managing Docker containers and stacks
across multiple remote hosts via SSH connections.
"""

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from pydantic import Field

try:
    from .core.cache_manager import get_cache_manager
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

    # All tool_params removed - they were unused
    from .resources import (
        DockerComposeResource,
        DockerContainersResource,
        DockerInfoResource,
        PortMappingResource,
    )
    from .services import ConfigService, ContainerService, HostService, StackService
    from .services.cleanup import CleanupService
    from .tools.logs import LogTools
except ImportError:
    from docker_mcp.core.cache_manager import get_cache_manager
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
    from docker_mcp.tools.logs import LogTools


# Import enum definitions
try:
    from .models.enums import ComposeAction, ContainerAction, HostAction
except ImportError:
    from docker_mcp.models.enums import ComposeAction, ContainerAction, HostAction


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
        Path(tempfile.gettempdir())
        / "docker-mcp"
        / str(os.getuid() if hasattr(os, "getuid") else "user"),  # Temp with user isolation
        Path(tempfile.gettempdir()) / "docker-mcp",  # Final fallback
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
        self._config_path: str = (
            config_path or os.getenv("DOCKER_HOSTS_CONFIG") or str(get_config_dir() / "hosts.yml")
        )

        # Use server logger (writes to mcp_server.log)
        self.logger = get_server_logger()

        # Initialize core managers
        self.context_manager = DockerContextManager(config)

        # Initialize cache manager (will be created asynchronously)
        self.cache_manager = None

        # Initialize service layer
        self.host_service = HostService(config)
        self.container_service = ContainerService(config, self.context_manager)
        self.stack_service = StackService(config, self.context_manager)
        self.config_service = ConfigService(config, self.context_manager)
        self.cleanup_service = CleanupService(config)

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

    async def _initialize_cache_manager(self) -> None:
        """Initialize the Docker cache manager asynchronously."""
        if self.cache_manager is None:
            self.cache_manager = await get_cache_manager(self.config, self.context_manager)

            # Set cache manager on services that can use it
            self.host_service.set_cache_manager(self.cache_manager)
            self.container_service.set_cache_manager(self.cache_manager)

            self.logger.info("Docker Cache Manager initialized and started")

    def _initialize_app(self) -> None:
        """Initialize FastMCP app, middleware, and register tools."""
        # Create FastMCP server
        self.app = FastMCP("Docker Context Manager")

        # Add middleware in logical order (first added = first executed)
        # Error handling first to catch all errors
        self.app.add_middleware(
            ErrorHandlingMiddleware(
                include_traceback=os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG",
                track_error_stats=True,
            )
        )

        # Rate limiting to protect against abuse
        rate_limit = float(os.getenv("RATE_LIMIT_PER_SECOND", "50.0"))
        self.app.add_middleware(
            RateLimitingMiddleware(
                max_requests_per_second=rate_limit,
                burst_capacity=int(rate_limit * 2),
                enable_global_limit=True,
            )
        )

        # Timing middleware to monitor performance
        slow_threshold = float(os.getenv("SLOW_REQUEST_THRESHOLD_MS", "5000.0"))
        self.app.add_middleware(
            TimingMiddleware(slow_request_threshold_ms=slow_threshold, track_statistics=True)
        )

        # Logging middleware last to log everything (including middleware processing)
        self.app.add_middleware(
            LoggingMiddleware(
                include_payloads=os.getenv("LOG_INCLUDE_PAYLOADS", "true").lower() == "true",
                max_payload_length=int(os.getenv("LOG_MAX_PAYLOAD_LENGTH", "1000")),
            )
        )

        self.logger.info(
            "FastMCP middleware initialized",
            error_handling=True,
            rate_limiting=f"{rate_limit} req/sec",
            timing_monitoring=f"{slow_threshold}ms threshold",
            logging="dual output (console + files)",
        )

        # Register consolidated tools (3 tools replace 13 individual tools)
        self.app.tool(
            self.docker_hosts,
            annotations={
                "title": "Docker Host Management",
                "readOnlyHint": False,  # Some actions (list, ports) read-only, others modify
                "destructiveHint": False,  # Most actions are safe, cleanup can be destructive
                "idempotentHint": False,  # Varies by action (add is not, list is)
                "openWorldHint": True,  # Connects to external Docker hosts via SSH
            },
        )
        self.app.tool(
            self.docker_container,
            annotations={
                "title": "Docker Container Management",
                "readOnlyHint": False,  # Some actions (list, info, logs) read-only, others modify
                "destructiveHint": False,  # Containers are ephemeral, operations are non-destructive
                "idempotentHint": False,  # Varies by action (start/stop not idempotent, list is)
                "openWorldHint": True,  # Connects to external Docker hosts
            },
        )
        self.app.tool(
            self.docker_compose,
            annotations={
                "title": "Docker Compose Stack Management",
                "readOnlyHint": False,  # Some actions (list, discover, logs) read-only, others modify
                "destructiveHint": True,  # down action destroys containers, migrate can remove source
                "idempotentHint": False,  # Varies by action (deploy can be, up/down are not)
                "openWorldHint": True,  # Connects to external Docker hosts and file systems
            },
        )

        # Register MCP resources for data access (complement tools with clean URI-based data retrieval)
        self._register_resources()

    def _register_resources(self) -> None:
        """Register MCP resources for data access.

        Resources provide clean, URI-based access to data without side effects.
        They complement tools by offering cacheable, parametrized data retrieval.
        """
        try:
            # Port mapping resource - ports://{host_id}
            port_resource = PortMappingResource(self.container_service, self)
            self.app.add_resource(port_resource)

            # Docker host info resource - docker://{host_id}/info
            info_resource = DockerInfoResource(
                context_manager=self.context_manager, host_service=self.host_service
            )
            self.app.add_resource(info_resource)

            # Docker containers resource - docker://{host_id}/containers
            containers_resource = DockerContainersResource(container_service=self.container_service)
            self.app.add_resource(containers_resource)

            # Docker compose resource - docker://{host_id}/compose
            compose_resource = DockerComposeResource(stack_service=self.stack_service)
            self.app.add_resource(compose_resource)

            self.logger.info(
                "MCP resources registered successfully",
                resources_count=4,
                uri_schemes=["ports://", "docker://"],
            )

        except Exception as e:
            self.logger.error("Failed to register MCP resources", error=str(e))
            # Don't fail the server startup, just log the error
            # Resources are optional enhancements to the tool-based API

    # Consolidated Tools Implementation

    async def docker_hosts(
        self,
        action: Annotated[
            str | HostAction | None,
            Field(default=None, description="Action to perform (defaults to list if not provided)"),
        ] = None,
        host_id: Annotated[str, Field(default="", description="Host identifier")] = "",
        ssh_host: Annotated[str, Field(default="", description="SSH hostname or IP address")] = "",
        ssh_user: Annotated[str, Field(default="", description="SSH username")] = "",
        ssh_port: Annotated[
            int, Field(default=22, ge=1, le=65535, description="SSH port number")
        ] = 22,
        ssh_key_path: Annotated[
            str, Field(default="", description="Path to SSH private key file")
        ] = "",
        description: Annotated[str, Field(default="", description="Host description")] = "",
        tags: Annotated[list[str], Field(default_factory=list, description="Host tags")] = None,
        compose_path: Annotated[
            str, Field(default="", description="Docker Compose file path")
        ] = "",
        appdata_path: Annotated[
            str, Field(default="", description="Application data storage path")
        ] = "",
        enabled: Annotated[bool, Field(default=True, description="Whether host is enabled")] = True,
        ssh_config_path: Annotated[
            str, Field(default="", description="Path to SSH config file")
        ] = "",
        selected_hosts: Annotated[
            str, Field(default="", description="Comma-separated list of hosts to select")
        ] = "",
        cleanup_type: Annotated[
            str, Field(default="", description="Type of cleanup to perform")
        ] = "",
        frequency: Annotated[str, Field(default="", description="Cleanup schedule frequency")] = "",
        time: Annotated[
            str, Field(default="", description="Cleanup schedule time in HH:MM format")
        ] = "",
        port: Annotated[
            int, Field(default=0, ge=0, le=65535, description="Port number to check availability")
        ] = 0,
    ) -> dict[str, Any]:
        """Simplified Docker hosts management tool.

        Actions:
        • list: List all configured Docker hosts
          - Required: none

        • add: Add a new Docker host (auto-runs test_connection and discover)
          - Required: host_id, ssh_host, ssh_user
          - Optional: ssh_port (default: 22), ssh_key_path, description, tags, enabled (default: true)

        • ports: List or check port usage on a host
          - Required: host_id
          - Optional: port (for availability check)

        • import_ssh: Import hosts from SSH config (auto-runs test_connection and discover for each)
          - Required: none
          - Optional: ssh_config_path, selected_hosts

        • cleanup: Docker system cleanup with integrated schedule management
          - Required: host_id, cleanup_type
          - Valid cleanup_type: "check" | "safe" | "moderate" | "aggressive"
          - For scheduling: cleanup_type, frequency, time
          - Valid frequency: "daily" | "weekly" | "monthly" | "custom"
          - Valid time: HH:MM format (24-hour, e.g., "02:00", "14:30")

        • test_connection: Test host connectivity (also runs discover)
          - Required: host_id

        • discover: Discover paths and capabilities
          - Optional: host_id (if not provided, discovers all hosts)
          - Discovers: compose_path, appdata_path, ZFS capabilities
          - Auto-tags: Adds "zfs" tag if ZFS detected

        • edit: Modify host configuration
          - Required: host_id
          - Optional: ssh_host, ssh_user, ssh_port, ssh_key_path, description, tags, compose_path, appdata_path, enabled

        • remove: Remove host from configuration
          - Required: host_id
        """
        # Parse and validate parameters using the parameter model
        try:
            params = DockerHostsParams(
                action=action if action is not None else HostAction.LIST,
                host_id=host_id,
                ssh_host=ssh_host,
                ssh_user=ssh_user,
                ssh_port=ssh_port,
                ssh_key_path=ssh_key_path if ssh_key_path else None,
                description=description,
                tags=tags or [],
                compose_path=compose_path if compose_path else None,
                appdata_path=appdata_path if appdata_path else None,
                enabled=enabled,
                port=port,
                cleanup_type=cleanup_type if cleanup_type else None,
                frequency=frequency if frequency else None,
                time=time if time else None,
                ssh_config_path=ssh_config_path if ssh_config_path else None,
                selected_hosts=selected_hosts if selected_hosts else None,
            )
            # Use validated enum from parameter model
            action = params.action
        except Exception as e:
            return {
                "success": False,
                "error": f"Parameter validation failed: {str(e)}",
                "action": str(action) if action else "unknown",
            }

        try:
            # Route to appropriate handler with validation
            if action == HostAction.LIST:
                return await self.host_service.list_docker_hosts()

            elif action == HostAction.ADD:
                # Validate required parameters for add action
                if not host_id:
                    return {"success": False, "error": "host_id is required for add action"}
                if not ssh_host:
                    return {"success": False, "error": "ssh_host is required for add action"}
                if not ssh_user:
                    return {"success": False, "error": "ssh_user is required for add action"}

                # Validate port range
                if not (1 <= ssh_port <= 65535):
                    return {
                        "success": False,
                        "error": f"ssh_port must be between 1 and 65535, got {ssh_port}",
                    }

                # Add host with auto-discovery
                result = await self.host_service.add_docker_host(
                    host_id,
                    ssh_host,
                    ssh_user,
                    ssh_port,
                    ssh_key_path,
                    description,
                    tags,
                    compose_path,
                    enabled,
                )

                # Auto-run discovery if host was added successfully (always enabled)
                if result.get("success"):
                    discovery_result = await self.host_service.discover_host_capabilities(host_id)
                    if discovery_result.get("success") and discovery_result.get("recommendations"):
                        result["discovery"] = discovery_result
                        result["message"] += " (Discovery completed - check recommendations)"

                return result

            elif action == HostAction.EDIT:
                # Validate required parameters for edit action
                if not host_id:
                    return {"success": False, "error": "host_id is required for edit action"}

                return await self.host_service.edit_docker_host(
                    host_id,
                    ssh_host,
                    ssh_user,
                    ssh_port,
                    ssh_key_path,
                    description,
                    tags,
                    compose_path,
                    appdata_path,
                    enabled,
                )

            elif action == HostAction.REMOVE:
                # Validate required parameters for remove action
                if not host_id:
                    return {"success": False, "error": "host_id is required for remove action"}

                return await self.host_service.remove_docker_host(host_id)

            elif action == HostAction.TEST_CONNECTION:
                # Validate required parameters for test_connection action
                if not host_id:
                    return {
                        "success": False,
                        "error": "host_id is required for test_connection action",
                    }

                return await self.host_service.test_connection(host_id)

            elif action == HostAction.DISCOVER:
                # If no host_id provided, discover all hosts
                if not host_id:
                    result = await self.host_service.discover_all_hosts()
                    return self._format_discover_all_result(result)

                # Otherwise discover specific host
                result = await self.host_service.discover_host_capabilities(host_id)
                return self._format_discover_result(result, host_id)

            elif action == HostAction.PORTS:
                # Validate required parameters for ports action
                if not host_id:
                    return {"success": False, "error": "host_id is required for ports action"}

                # Handle sub-actions: "list" (default) or "check"
                # For ports check, port parameter must be provided
                if port > 0:
                    # Check specific port availability
                    return await self.container_service.check_port_availability(host_id, port)
                else:
                    # List all ports (simplified - always include stopped containers)
                    result = await self.container_service.list_host_ports(host_id)
                    # Convert ToolResult to dict for consistency
                    if hasattr(result, "structured_content"):
                        return result.structured_content or {
                            "success": True,
                            "data": "No structured content",
                        }
                    return result

            elif action == HostAction.IMPORT_SSH:
                result = await self.config_service.import_ssh_config(
                    ssh_config_path, selected_hosts
                )
                # Convert ToolResult to dict for consistency
                if hasattr(result, "structured_content"):
                    import_result = result.structured_content or {
                        "success": True,
                        "data": str(result.content),
                    }
                else:
                    import_result = result

                # Auto-run discovery on imported hosts if import was successful
                if import_result.get("success") and import_result.get("imported_hosts"):
                    discovered_hosts = []
                    for host_info in import_result["imported_hosts"]:
                        host_id = host_info["host_id"]

                        # Run test_connection and discover for each imported host
                        try:
                            test_result = await self.host_service.test_connection(host_id)
                            discovery_result = await self.host_service.discover_host_capabilities(
                                host_id
                            )

                            discovered_hosts.append(
                                {
                                    "host_id": host_id,
                                    "connection_test": test_result.get("success", False),
                                    "discovery": discovery_result.get("success", False),
                                    "recommendations": discovery_result.get("recommendations", []),
                                }
                            )
                        except Exception as e:
                            self.logger.error(
                                "Auto-discovery failed for imported host",
                                host_id=host_id,
                                error=str(e),
                            )
                            discovered_hosts.append(
                                {
                                    "host_id": host_id,
                                    "connection_test": False,
                                    "discovery": False,
                                    "error": str(e),
                                }
                            )

                    # Add discovery results to import result
                    import_result["auto_discovery"] = {
                        "completed": True,
                        "results": discovered_hosts,
                    }
                    import_result["message"] = (
                        import_result.get("message", "")
                        + " (Auto-discovery completed for imported hosts)"
                    )

                return import_result

            elif action == HostAction.CLEANUP:
                # Handle cleanup sub-actions:
                # - "cleanup check <host_id>" -> Check disk usage
                # - "cleanup <cleanup_type> <host_id>" -> Execute cleanup
                # - "cleanup schedule" with frequency/time -> Add schedule
                # - "cleanup schedule" without frequency/time -> List or remove schedules

                # Handle schedule operations when frequency is provided (add schedule)
                if frequency and time:
                    if not host_id or not cleanup_type:
                        return {
                            "success": False,
                            "error": "host_id and cleanup_type required for scheduling",
                        }
                    if cleanup_type not in ["safe", "moderate"]:
                        return {
                            "success": False,
                            "error": "Only 'safe' and 'moderate' cleanup types can be scheduled",
                        }
                    return await self.cleanup_service.add_schedule(
                        host_id, cleanup_type, frequency, time
                    )

                # Handle schedule list when no host_id but no frequency (list all schedules)
                elif not host_id and not frequency and not cleanup_type:
                    return await self.cleanup_service.list_schedules()

                # Handle schedule remove when host_id but no frequency/cleanup_type
                elif host_id and not frequency and not cleanup_type:
                    return await self.cleanup_service.remove_schedule(host_id)

                # Handle cleanup operations
                else:
                    if not host_id:
                        return {"success": False, "error": "host_id is required for cleanup action"}
                    if not cleanup_type:
                        return {
                            "success": False,
                            "error": "cleanup_type is required for cleanup action",
                        }
                    if cleanup_type not in ["check", "safe", "moderate", "aggressive"]:
                        return {
                            "success": False,
                            "error": "cleanup_type must be one of: check, safe, moderate, aggressive",
                        }

                    if cleanup_type == "check":
                        # Show disk usage for cleanup planning
                        return await self.cleanup_service.docker_disk_usage(host_id)
                    else:
                        # Perform actual cleanup
                        return await self.cleanup_service.docker_cleanup(host_id, cleanup_type)

            else:
                return {
                    "success": False,
                    "error": f"Unknown action: {action}",
                    "valid_actions": [
                        "list",
                        "add",
                        "edit",
                        "remove",
                        "test_connection",
                        "discover",
                        "ports",
                        "import_ssh",
                        "cleanup",
                    ],
                }

        except Exception as e:
            self.logger.error("docker_hosts tool error", action=action, error=str(e))
            return {"success": False, "error": f"Tool execution failed: {str(e)}", "action": action}

    async def docker_container(
        self,
        action: Annotated[str | ContainerAction, Field(description="Action to perform")],
        host_id: Annotated[str, Field(default="", description="Host identifier")] = "",
        container_id: Annotated[str, Field(default="", description="Container identifier")] = "",
        all_containers: Annotated[
            bool, Field(default=False, description="Include all containers, not just running")
        ] = False,
        limit: Annotated[
            int, Field(default=20, ge=1, le=1000, description="Maximum number of results to return")
        ] = 20,
        offset: Annotated[int, Field(default=0, ge=0, description="Number of results to skip")] = 0,
        follow: Annotated[bool, Field(default=False, description="Follow log output")] = False,
        lines: Annotated[
            int, Field(default=100, ge=1, le=10000, description="Number of log lines to retrieve")
        ] = 100,
        force: Annotated[bool, Field(default=False, description="Force the operation")] = False,
        timeout: Annotated[
            int, Field(default=10, ge=1, le=300, description="Operation timeout in seconds")
        ] = 10,
    ) -> dict[str, Any]:
        """Consolidated Docker container management tool.

        Actions:
        • list: List containers on a host
          - Required: host_id
          - Optional: all_containers, limit, offset

        • info: Get container information
          - Required: host_id, container_id

        • start: Start a container
          - Required: host_id, container_id
          - Optional: force, timeout

        • stop: Stop a container
          - Required: host_id, container_id
          - Optional: force, timeout

        • restart: Restart a container
          - Required: host_id, container_id
          - Optional: force, timeout

        • build: Build/rebuild a container
          - Required: host_id, container_id
          - Optional: force, timeout

        • logs: Get container logs
          - Required: host_id, container_id
          - Optional: follow, lines
        """
        # Parse and validate parameters using the parameter model
        try:
            params = DockerContainerParams(
                action=action,
                host_id=host_id,
                container_id=container_id,
                all_containers=all_containers,
                limit=limit,
                offset=offset,
                follow=follow,
                lines=lines,
                force=force,
                timeout=timeout,
            )
            # Use validated enum from parameter model
            action = params.action
        except Exception as e:
            return {
                "success": False,
                "error": f"Parameter validation failed: {str(e)}",
                "action": str(action) if action else "unknown",
            }

        try:
            # Route to appropriate handler with validation
            if action == ContainerAction.LIST:
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
                if hasattr(result, "structured_content"):
                    return result.structured_content or {
                        "success": True,
                        "data": "No structured content",
                    }
                return result

            elif action == ContainerAction.INFO:
                # Validate required parameters for info action
                if not host_id:
                    return {"success": False, "error": "host_id is required for info action"}
                if not container_id:
                    return {"success": False, "error": "container_id is required for info action"}

                result = await self.get_container_info(host_id, container_id)
                # Convert ToolResult to dict for consistency
                if hasattr(result, "structured_content"):
                    return result.structured_content or {
                        "success": True,
                        "data": "No structured content",
                    }
                return result

            elif action in [
                ContainerAction.START,
                ContainerAction.STOP,
                ContainerAction.RESTART,
                ContainerAction.BUILD,
                ContainerAction.REMOVE,
            ]:
                # Validate required parameters for container management actions
                if not host_id:
                    return {"success": False, "error": f"host_id is required for {action} action"}
                if not container_id:
                    return {
                        "success": False,
                        "error": f"container_id is required for {action} action",
                    }

                # Validate timeout parameter
                if timeout < 1 or timeout > 300:
                    return {"success": False, "error": "timeout must be between 1 and 300 seconds"}

                result = await self.manage_container(
                    host_id, container_id, action.value, force, timeout
                )
                # Convert ToolResult to dict for consistency
                if hasattr(result, "structured_content"):
                    return result.structured_content or {
                        "success": True,
                        "data": "No structured content",
                    }
                return result

            elif action == ContainerAction.LOGS:
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
                    return {
                        "success": False,
                        "error": "container_id is required for pull action (image name)",
                    }

                # For pull, container_id is actually the image name
                result = await self.pull_image(host_id, container_id)
                # Convert ToolResult to dict for consistency
                if hasattr(result, "structured_content"):
                    return result.structured_content or {
                        "success": True,
                        "data": "No structured content",
                    }
                return result

        except Exception as e:
            self.logger.error(
                "docker_container tool error",
                action=action,
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {
                "success": False,
                "error": f"Tool execution failed: {str(e)}",
                "action": action,
                "host_id": host_id,
                "container_id": container_id,
            }

    async def docker_compose(
        self,
        action: Annotated[str | ComposeAction, Field(description="Action to perform")],
        host_id: Annotated[str, Field(default="", description="Host identifier")] = "",
        stack_name: Annotated[str, Field(default="", description="Stack name")] = "",
        compose_content: Annotated[
            str, Field(default="", description="Docker Compose file content")
        ] = "",
        environment: Annotated[
            dict[str, str], Field(default_factory=dict, description="Environment variables")
        ] = None,
        pull_images: Annotated[
            bool, Field(default=True, description="Pull images before deploying")
        ] = True,
        recreate: Annotated[bool, Field(default=False, description="Recreate containers")] = False,
        follow: Annotated[bool, Field(default=False, description="Follow log output")] = False,
        lines: Annotated[
            int, Field(default=100, ge=1, le=10000, description="Number of log lines to retrieve")
        ] = 100,
        dry_run: Annotated[
            bool, Field(default=False, description="Perform a dry run without making changes")
        ] = False,
        options: Annotated[
            dict[str, str],
            Field(default_factory=dict, description="Additional options for the operation"),
        ] = None,
        target_host_id: Annotated[
            str, Field(default="", description="Target host ID for migration operations")
        ] = "",
        remove_source: Annotated[
            bool, Field(default=False, description="Remove source stack after migration")
        ] = False,
        skip_stop_source: Annotated[
            bool, Field(default=False, description="Skip stopping source stack before migration")
        ] = False,
        start_target: Annotated[
            bool, Field(default=True, description="Start target stack after migration")
        ] = True,
    ) -> dict[str, Any]:
        """Consolidated Docker Compose stack management tool.

        Actions:
        • list: List stacks on a host
          - Required: host_id

        • deploy: Deploy a stack
          - Required: host_id, stack_name, compose_content
          - Optional: environment, pull_images, recreate

        • up/down/restart/build: Manage stack lifecycle
          - Required: host_id, stack_name
          - Optional: options

        • discover: Discover compose paths on a host
          - Required: host_id

        • logs: Get stack logs
          - Required: host_id, stack_name
          - Optional: follow, lines

        • migrate: Migrate stack between hosts
          - Required: host_id, target_host_id, stack_name
          - Optional: remove_source, skip_stop_source, start_target, dry_run
        """
        # Parse and validate parameters using the parameter model
        try:
            params = DockerComposeParams(
                action=action,
                host_id=host_id,
                stack_name=stack_name,
                compose_content=compose_content,
                environment=environment or {},
                pull_images=pull_images,
                recreate=recreate,
                follow=follow,
                lines=lines,
                dry_run=dry_run,
                options=options or {},
                target_host_id=target_host_id,
                remove_source=remove_source,
                skip_stop_source=skip_stop_source,
                start_target=start_target,
            )
            # Use validated enum from parameter model
            action = params.action
        except Exception as e:
            return {
                "success": False,
                "error": f"Parameter validation failed: {str(e)}",
                "action": str(action) if action else "unknown",
            }

        try:
            # Route to appropriate handler with validation
            if action == ComposeAction.LIST:
                # Validate required parameters for list action
                if not host_id:
                    return {"success": False, "error": "host_id is required for list action"}

                result = await self.list_stacks(host_id)
                # Convert ToolResult to dict for consistency
                if hasattr(result, "structured_content"):
                    return result.structured_content or {
                        "success": True,
                        "data": "No structured content",
                    }
                return result

            elif action == ComposeAction.VIEW:
                # Validate required parameters for view action
                if not host_id:
                    return {"success": False, "error": "host_id is required for view action"}
                if not stack_name:
                    return {"success": False, "error": "stack_name is required for view action"}

                result = await self.stack_service.get_stack_compose_file(host_id, stack_name)
                # Convert ToolResult to dict for consistency
                if hasattr(result, "structured_content"):
                    return result.structured_content or {
                        "success": True,
                        "data": "No structured content",
                    }
                return result

            elif action == ComposeAction.DEPLOY:
                # Validate required parameters for deploy action
                if not host_id:
                    return {"success": False, "error": "host_id is required for deploy action"}
                if not stack_name:
                    return {"success": False, "error": "stack_name is required for deploy action"}
                if not compose_content:
                    return {
                        "success": False,
                        "error": "compose_content is required for deploy action",
                    }

                # Validate stack name format (DNS compliance - no underscores allowed)
                if not re.match(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", stack_name):
                    return {
                        "success": False,
                        "error": "stack_name must be DNS-compliant: lowercase letters, numbers, and hyphens only (no underscores)",
                    }

                result = await self.deploy_stack(
                    host_id, stack_name, compose_content, environment, pull_images, recreate
                )
                # Convert ToolResult to dict for consistency
                if hasattr(result, "structured_content"):
                    return result.structured_content or {
                        "success": True,
                        "data": "No structured content",
                    }
                return result

            elif action in ["up", "down", "restart", "build", "pull"]:
                # Validate required parameters for stack management actions
                if not host_id:
                    return {"success": False, "error": f"host_id is required for {action} action"}
                if not stack_name:
                    return {
                        "success": False,
                        "error": f"stack_name is required for {action} action",
                    }

                result = await self.manage_stack(host_id, stack_name, action.value, options)
                # Convert ToolResult to dict for consistency
                if hasattr(result, "structured_content"):
                    return result.structured_content or {
                        "success": True,
                        "data": "No structured content",
                    }
                return result

            elif action == ComposeAction.LOGS:
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
                    logs_options = {"tail": str(lines), "follow": follow}

                    result = await self.stack_service.manage_stack(
                        host_id, stack_name, "logs", logs_options
                    )

                    # Format the result for logs
                    if hasattr(result, "structured_content") and result.structured_content:
                        logs_data = result.structured_content
                        # Extract logs from the result
                        if "output" in logs_data:
                            logs_lines = (
                                logs_data["output"].split("\n") if logs_data["output"] else []
                            )
                            return {
                                "success": True,
                                "host_id": host_id,
                                "stack_name": stack_name,
                                "logs": logs_lines,
                                "lines_requested": lines,
                                "lines_returned": len(logs_lines),
                                "follow": follow,
                            }
                        else:
                            return logs_data
                    else:
                        return {"success": False, "error": "Failed to retrieve stack logs"}

                except Exception as e:
                    self.logger.error(
                        "docker_compose logs error",
                        host_id=host_id,
                        stack_name=stack_name,
                        error=str(e),
                    )
                    return {"success": False, "error": f"Failed to get stack logs: {str(e)}"}

            elif action == ComposeAction.MIGRATE:
                # Validate required parameters for migrate action
                if not host_id:
                    return {
                        "success": False,
                        "error": "host_id (source) is required for migrate action",
                    }
                if not target_host_id:
                    return {
                        "success": False,
                        "error": "target_host_id is required for migrate action",
                    }
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
                    dry_run=dry_run,
                )

                # Convert ToolResult to dict for consistency
                if hasattr(result, "structured_content"):
                    return result.structured_content or {
                        "success": True,
                        "data": "Migration completed",
                    }
                return result

            elif action in [
                ComposeAction.UP,
                ComposeAction.DOWN,
                ComposeAction.RESTART,
                ComposeAction.BUILD,
            ]:
                # Validate required parameters for stack lifecycle actions
                if not host_id:
                    return {
                        "success": False,
                        "error": "host_id is required for stack lifecycle actions",
                    }
                if not stack_name:
                    return {
                        "success": False,
                        "error": "stack_name is required for stack lifecycle actions",
                    }

                # Use the stack service to manage stack lifecycle
                result = await self.stack_service.manage_stack(
                    host_id=host_id,
                    stack_name=stack_name,
                    action=action.value,  # Convert enum to string value
                    options=options,
                )

                # Convert ToolResult to dict for consistency
                if hasattr(result, "structured_content"):
                    return result.structured_content or {
                        "success": True,
                        "data": f"Stack {action.value} completed",
                    }
                return result

            else:
                return {
                    "success": False,
                    "error": f"Unsupported action: {action.value}",
                    "supported_actions": [a.value for a in ComposeAction],
                }

        except Exception as e:
            self.logger.error(
                "docker_compose tool error",
                action=action,
                host_id=host_id,
                stack_name=stack_name,
                error=str(e),
            )
            return {
                "success": False,
                "error": f"Tool execution failed: {str(e)}",
                "action": action,
                "host_id": host_id,
                "stack_name": stack_name,
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
        compose_path: str | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Add a new Docker host for management."""
        return await self.host_service.add_docker_host(
            host_id,
            ssh_host,
            ssh_user,
            ssh_port,
            ssh_key_path,
            description,
            tags,
            compose_path,
            enabled,
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
                timestamps=False,
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
        return await self.container_service.manage_container(
            host_id, container_id, action, force, timeout
        )

    async def pull_image(self, host_id: str, image_name: str) -> ToolResult:
        """Pull a Docker image on a remote host."""
        return await self.container_service.pull_image(host_id, image_name)

    async def list_host_ports(self, host_id: str, include_stopped: bool = False) -> ToolResult:
        """List all ports currently in use by containers on a Docker host."""
        # Note: ContainerService.list_host_ports only takes host_id (includes stopped containers by default)
        return await self.container_service.list_host_ports(host_id)

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

        # Update remaining tools with new config
        self.log_tools.config = new_config

        self.logger.info("Configuration updated", hosts=list(new_config.hosts.keys()))

    async def start_hot_reload(self) -> None:
        """Start hot reload watcher if configured."""
        await self.hot_reload_manager.start_hot_reload()

    async def stop_hot_reload(self) -> None:
        """Stop hot reload watcher."""
        await self.hot_reload_manager.stop_hot_reload()

    def _format_discover_all_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Format discover all hosts result for user-friendly display."""
        if not result.get("success", False):
            return result

        # Generate summary text for user display
        summary_lines = []
        summary_lines.append(
            f"🔍 Discovery Summary: {result.get('summary', 'No summary available')}"
        )

        if result.get("total_hosts", 0) > 0:
            summary_lines.append(f"   Successful: {result.get('successful_discoveries', 0)}")
            summary_lines.append(f"   Failed: {result.get('failed_discoveries', 0)}")
            summary_lines.append("")

        discoveries = result.get("discoveries", {})
        for host_id, discovery in discoveries.items():
            if discovery.get("success", False):
                summary_lines.append(f"✅ {host_id}:")
                recommendations = discovery.get("recommendations", [])
                if recommendations:
                    for rec in recommendations:
                        if rec["type"] == "compose_path":
                            summary_lines.append(f"   📂 Compose path: {rec['value']}")
                        elif rec["type"] == "appdata_path":
                            summary_lines.append(f"   💾 Appdata path: {rec['value']}")
                        elif rec["type"] == "zfs_config":
                            zfs_info = f"ZFS dataset: {rec.get('zfs_dataset', 'N/A')}"
                            if rec.get("tag_added"):
                                zfs_info += " (tag added)"
                            summary_lines.append(f"   🗂️  {zfs_info}")
                else:
                    summary_lines.append("   No recommendations available")
            else:
                summary_lines.append(f"❌ {host_id}: {discovery.get('error', 'Discovery failed')}")
            summary_lines.append("")

        return {
            "success": True,
            "message": "\n".join(summary_lines),
            **result,  # Include all original data
        }

    def _format_discover_result(self, result: dict[str, Any], host_id: str) -> dict[str, Any]:
        """Format single host discovery result for user-friendly display."""
        if not result.get("success", False):
            return result

        # Generate summary text for user display
        summary_lines = []
        summary_lines.append(f"🔍 Discovery Results for {host_id}:")
        summary_lines.append("")

        recommendations = result.get("recommendations", [])
        if recommendations:
            for rec in recommendations:
                if rec["type"] == "compose_path":
                    summary_lines.append(f"📂 Compose path: {rec['value']}")
                    summary_lines.append(
                        f"   Use: docker_hosts edit {host_id} --compose_path '{rec['value']}'"
                    )
                elif rec["type"] == "appdata_path":
                    summary_lines.append(f"💾 Appdata path: {rec['value']}")
                    summary_lines.append(
                        f"   Use: docker_hosts edit {host_id} --appdata_path '{rec['value']}'"
                    )
                elif rec["type"] == "zfs_config":
                    zfs_info = f"ZFS dataset: {rec.get('zfs_dataset', 'N/A')}"
                    if rec.get("tag_added"):
                        zfs_info += " (tag automatically added)"
                    summary_lines.append(f"🗂️  {zfs_info}")
                summary_lines.append("")
        else:
            summary_lines.append("No automatic recommendations available.")
            summary_lines.append("")

        # Add discovery details
        compose_discovery = result.get("compose_discovery", {})
        if compose_discovery.get("paths"):
            summary_lines.append(f"Found compose paths: {', '.join(compose_discovery['paths'])}")

        appdata_discovery = result.get("appdata_discovery", {})
        if appdata_discovery.get("paths"):
            summary_lines.append(f"Found appdata paths: {', '.join(appdata_discovery['paths'])}")

        zfs_discovery = result.get("zfs_discovery", {})
        if zfs_discovery.get("capable"):
            summary_lines.append(
                f"ZFS capable: Yes (pools: {', '.join(zfs_discovery.get('pools', []))})"
            )
        else:
            summary_lines.append("ZFS capable: No")

        return {
            "success": True,
            "message": "\n".join(summary_lines),
            **result,  # Include all original data
        }

    def run(self) -> None:
        """Run the FastMCP server."""
        try:
            # Initialize FastMCP app first
            self._initialize_app()

            # Initialize cache manager synchronously before FastMCP starts
            import asyncio

            # Create and start cache manager in separate loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Initialize cache manager and start background tasks
            loop.run_until_complete(self._initialize_cache_manager())

            # Don't close the loop - let cache manager run in background
            # FastMCP will use the same event loop

            self.logger.info(
                "Starting Docker MCP Server",
                host=self.config.server.host,
                port=self.config.server.port,
            )

            # Start FastMCP server (this handles its own event loop)
            self.app.run(
                transport="streamable-http",
                host=self.config.server.host,
                port=self.config.server.port,
            )

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
        str(Path(tempfile.gettempdir()) / "docker-mcp-logs"),  # System fallback
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
        logger.warning("Unable to create log directory, using console-only logging")
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
        setup_logging(log_dir=log_dir, log_level=args.log_level, max_file_size_mb=max_file_size_mb)
        logger = get_server_logger()

        # Log successful initialization with configuration details
        logger.info(
            "Logging system initialized",
            log_dir=log_dir,
            log_level=args.log_level,
            max_file_size_mb=max_file_size_mb,
            console_logging=True,
            file_logging=log_dir is not None,
        )
    except Exception as e:
        logger.warning(f"Logging setup failed ({e}), using basic console logging")
        import logging

        logging.basicConfig(
            level=getattr(logging, args.log_level.upper(), logging.INFO),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
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
            logger.info("✅ Configuration is valid")
            return

        # Create and run server (hot reload always enabled)
        config_path_for_reload = args.config or os.getenv(
            "DOCKER_HOSTS_CONFIG", str(get_config_dir() / "hosts.yml")
        )

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
