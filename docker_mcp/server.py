"""
FastMCP Docker SSH Manager Server

A production-ready FastMCP server for managing Docker containers and stacks
across multiple remote hosts via SSH connections.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Annotated, Optional, List, Dict

import structlog
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from pydantic import Field

try:
    from .core.config_loader import DockerMCPConfig, load_config
    from .core.docker_context import DockerContextManager
    from .core.file_watcher import HotReloadManager
    from .core.logging_config import setup_logging, get_server_logger
    from .middleware import (
        LoggingMiddleware,
        ErrorHandlingMiddleware,
        TimingMiddleware,
        RateLimitingMiddleware
    )
    from .models.params import DockerHostsParams, DockerContainerParams, DockerComposeParams
    from .services import ConfigService, ContainerService, HostService, StackService
    from .services.cleanup import CleanupService
    from .services.schedule import ScheduleService
    from .tools.logs import LogTools
except ImportError:
    from docker_mcp.core.config_loader import DockerMCPConfig, load_config
    from docker_mcp.core.docker_context import DockerContextManager
    from docker_mcp.core.file_watcher import HotReloadManager
    from docker_mcp.core.logging_config import setup_logging, get_server_logger
    from docker_mcp.middleware import (
        LoggingMiddleware,
        ErrorHandlingMiddleware,
        TimingMiddleware,
        RateLimitingMiddleware
    )
    from docker_mcp.models.params import DockerHostsParams, DockerContainerParams, DockerComposeParams
    from docker_mcp.services import ConfigService, ContainerService, HostService, StackService
    from docker_mcp.services.cleanup import CleanupService
    from docker_mcp.services.schedule import ScheduleService
    from docker_mcp.tools.logs import LogTools


def get_data_dir() -> Path:
    """Get data directory based on environment."""
    # Check for explicit data directory override first
    if fastmcp_data_dir := os.getenv("FASTMCP_DATA_DIR"):
        return Path(fastmcp_data_dir)
    
    # Check if running in container with explicit truthy check
    docker_container = os.getenv("DOCKER_CONTAINER", "").lower()
    if docker_container in ("1", "true", "yes", "on"):
        return Path("/app/data")
    else:
        # Local development path
        return Path.home() / ".docker-mcp" / "data"


def get_config_dir() -> Path:
    """Get config directory based on environment."""
    # Check for explicit config directory override first
    if fastmcp_config_dir := os.getenv("FASTMCP_CONFIG_DIR"):
        return Path(fastmcp_config_dir)
    
    # Check if running in container with explicit truthy check
    docker_container = os.getenv("DOCKER_CONTAINER", "").lower()
    if docker_container in ("1", "true", "yes", "on"):
        return Path("/app/config")
    else:
        # Local development - use project config dir
        return Path("config")


class DockerMCPServer:
    """FastMCP server for Docker management via Docker contexts."""

    def __init__(self, config: DockerMCPConfig, config_path: str | None = None):
        self.config = config
        self._config_path: str = config_path or os.getenv("DOCKER_HOSTS_CONFIG") or "config/hosts.yml"
        
        
        # Use server logger (writes to mcp_server.log)
        self.logger = get_server_logger()

        # Initialize core managers
        self.context_manager = DockerContextManager(config)

        # Initialize service layer
        self.host_service = HostService(config)
        self.container_service = ContainerService(config, self.context_manager)
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

    # Consolidated Tools Implementation
    
    async def docker_hosts(
        self,
        action: Annotated[str, Field(description="Action to perform (list, add, ports, compose_path, import_ssh, cleanup, disk_usage, schedule)")],
        host_id: Annotated[str, Field(default="", description="Host identifier **(used by: add, ports, compose_path)**")] = "",
        ssh_host: Annotated[str, Field(default="", description="SSH hostname or IP address **(used by: add)**")] = "",
        ssh_user: Annotated[str, Field(default="", description="SSH username **(used by: add)**")] = "",
        ssh_port: Annotated[int, Field(default=22, ge=1, le=65535, description="SSH port number **(used by: add)**")] = 22,
        ssh_key_path: Annotated[Optional[str], Field(default=None, description="Path to SSH private key file **(used by: add)**")] = None,
        description: Annotated[str, Field(default="", description="Host description **(used by: add)**")] = "",
        tags: Annotated[List[str], Field(default_factory=list, description="Host tags **(used by: add)**")] = None,
        test_connection: Annotated[bool, Field(default=True, description="Test connection when adding host **(used by: add)**")] = True,
        include_stopped: Annotated[bool, Field(default=False, description="Include stopped containers in listings **(used by: ports)**")] = False,
        compose_path: Annotated[Optional[str], Field(default=None, description="Docker Compose file path **(used by: add, compose_path)**")] = None,
        enabled: Annotated[bool, Field(default=True, description="Whether host is enabled **(used by: add)**")] = True,
        ssh_config_path: Annotated[Optional[str], Field(default=None, description="Path to SSH config file **(used by: import_ssh)**")] = None,
        selected_hosts: Annotated[Optional[str], Field(default=None, description="Comma-separated list of hosts to select **(used by: import_ssh)**")] = None,
        compose_path_overrides: Annotated[Dict[str, str], Field(default_factory=dict, description="Per-host compose path overrides **(used by: import_ssh)**")] = None,
        auto_confirm: Annotated[bool, Field(default=False, description="Auto-confirm operations without prompting **(used by: add, import_ssh)**")] = False,
        cleanup_type: Annotated[Optional[str], Field(default=None, description="Type of cleanup to perform (check, safe, moderate, aggressive) **(used by: cleanup)**")] = None,
        schedule_action: Annotated[Optional[str], Field(default=None, description="Schedule action to perform (add, remove, list, enable, disable) **(used by: schedule)**")] = None,
        schedule_frequency: Annotated[Optional[str], Field(default=None, description="Cleanup frequency (daily, weekly, monthly, custom) **(used by: schedule add)**")] = None,
        schedule_time: Annotated[Optional[str], Field(default=None, description="Time to run cleanup (e.g., '02:00') **(used by: schedule add)**")] = None,
        schedule_id: Annotated[Optional[str], Field(default=None, description="Schedule identifier for management **(used by: schedule remove/enable/disable)**")] = None,
    ) -> dict[str, Any]:
        """Consolidated Docker hosts management tool.
        
        Actions:
        - list: List all configured Docker hosts  
        - add: Add a new Docker host (requires: host_id, ssh_host, ssh_user; optional: ssh_port, ssh_key_path, description, tags, compose_path, enabled)
        - ports: List port mappings for a host (requires: host_id)
        - compose_path: Update host compose path (requires: host_id, compose_path)
        - import_ssh: Import hosts from SSH config
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
                    
                result = await self.list_host_ports(host_id, include_stopped)
                # Convert ToolResult to dict for consistency
                if hasattr(result, 'content') and hasattr(result, 'structured_content'):
                    return result.structured_content or {"success": True, "data": str(result.content)}
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
                    return {"success": False, "error": f"cleanup_type must be one of: check, safe, moderate, aggressive"}
                
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
                    return {"success": False, "error": f"schedule_action must be one of: add, remove, list, enable, disable"}
                
                return await self.schedule_service.handle_schedule_action(
                    schedule_action, host_id, cleanup_type, 
                    schedule_frequency, schedule_time, schedule_id
                )
                
        except Exception as e:
            self.logger.error("docker_hosts tool error", action=action, error=str(e))
            return {
                "success": False, 
                "error": f"Tool execution failed: {str(e)}",
                "action": action
            }

    async def docker_container(
        self,
        action: Annotated[str, Field(description="Action to perform (list, info, start, stop, restart, build, logs)")],
        host_id: Annotated[str, Field(default="", description="Host identifier **(required for all actions)**")] = "",
        container_id: Annotated[str, Field(default="", description="Container identifier **(used by: info, start, stop, restart, build, logs)**")] = "",
        all_containers: Annotated[bool, Field(default=False, description="Include all containers, not just running **(used by: list)**")] = False,
        limit: Annotated[int, Field(default=20, ge=1, le=1000, description="Maximum number of results to return **(used by: list)**")] = 20,
        offset: Annotated[int, Field(default=0, ge=0, description="Number of results to skip **(used by: list)**")] = 0,
        follow: Annotated[bool, Field(default=False, description="Follow log output **(used by: logs)**")] = False,
        lines: Annotated[int, Field(default=100, ge=1, le=10000, description="Number of log lines to retrieve **(used by: logs)**")] = 100,
        force: Annotated[bool, Field(default=False, description="Force the operation **(used by: stop)**")] = False,
        timeout: Annotated[int, Field(default=10, ge=1, le=300, description="Operation timeout in seconds **(used by: start, stop, restart)**")] = 10
    ) -> dict[str, Any]:
        """Consolidated Docker container management tool.
        
        Actions:
        - list: List containers on a host (requires: host_id)
        - info: Get container information (requires: host_id, container_id)
        - start/stop/restart/build: Manage container (requires: host_id, container_id)
        - logs: Get container logs (requires: host_id, container_id)
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
                if limit < 1 or limit > 100:
                    return {"success": False, "error": "limit must be between 1 and 100"}
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
                    return {"success": False, "error": "lines must be between 1 and 1000"}
                    
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
        action: Annotated[str, Field(description="Action to perform (list, deploy, up, down, restart, build, discover, logs, migrate)")],
        host_id: Annotated[str, Field(default="", description="Host identifier **(required for all actions)**")] = "",
        stack_name: Annotated[str, Field(default="", description="Stack name **(used by: deploy, up, down, restart, build, logs, migrate)**")] = "",
        compose_content: Annotated[str, Field(default="", description="Docker Compose file content **(used by: deploy)**")] = "",
        environment: Annotated[Dict[str, str], Field(default_factory=dict, description="Environment variables **(used by: deploy, up)**")] = None,
        pull_images: Annotated[bool, Field(default=True, description="Pull images before deploying **(used by: deploy, up)**")] = True,
        recreate: Annotated[bool, Field(default=False, description="Recreate containers **(used by: up)**")] = False,
        follow: Annotated[bool, Field(default=False, description="Follow log output **(used by: logs)**")] = False,
        lines: Annotated[int, Field(default=100, ge=1, le=10000, description="Number of log lines to retrieve **(used by: logs)**")] = 100,
        dry_run: Annotated[bool, Field(default=False, description="Perform a dry run without making changes **(used by: migrate)**")] = False,
        options: Annotated[Optional[Dict[str, str]], Field(default=None, description="Additional options for the operation **(used by: up, down, restart, build)**")] = None,
        target_host_id: Annotated[str, Field(default="", description="Target host ID for migration **(used by: migrate)**")] = "",
        remove_source: Annotated[bool, Field(default=False, description="Remove source stack after migration **(used by: migrate)**")] = False,
        skip_stop_source: Annotated[bool, Field(default=False, description="Skip stopping source stack before migration **(used by: migrate)**")] = False,
        start_target: Annotated[bool, Field(default=True, description="Start target stack after migration **(used by: migrate)**")] = True
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
                    return {"success": False, "error": "lines must be between 1 and 1000"}
                    
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
                host_id, container_id, lines, None, False
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
    default_config = os.getenv("DOCKER_HOSTS_CONFIG", "config/hosts.yml")

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

    # Setup unified logging (console + files)
    from docker_mcp.core.logging_config import setup_logging, get_server_logger
    setup_logging(
        log_dir=os.getenv("LOG_DIR", str(get_data_dir() / "logs")),
        log_level=args.log_level,
        max_file_size_mb=int(os.getenv("LOG_FILE_SIZE_MB", "10"))
    )
    logger = get_server_logger()

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
        config_path_for_reload = args.config or os.getenv("DOCKER_HOSTS_CONFIG", "config/hosts.yml")

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
