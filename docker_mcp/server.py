"""
FastMCP Docker SSH Manager Server

A production-ready FastMCP server for managing Docker containers and stacks
across multiple remote hosts via SSH connections.
"""

import argparse
import os
import sys
from typing import Any

import structlog
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult

try:
    from .core.config import DockerMCPConfig, load_config
    from .core.docker_context import DockerContextManager
    from .core.file_watcher import HotReloadManager
    from .core.logging_config import setup_logging, get_server_logger
    from .middleware import (
        LoggingMiddleware,
        ErrorHandlingMiddleware,
        TimingMiddleware,
        RateLimitingMiddleware
    )
    from .services import ConfigService, ContainerService, HostService, StackService
    from .tools.logs import LogTools
except ImportError:
    from docker_mcp.core.config import DockerMCPConfig, load_config
    from docker_mcp.core.docker_context import DockerContextManager
    from docker_mcp.core.file_watcher import HotReloadManager
    from docker_mcp.core.logging_config import setup_logging, get_server_logger
    from docker_mcp.middleware import (
        LoggingMiddleware,
        ErrorHandlingMiddleware,
        TimingMiddleware,
        RateLimitingMiddleware
    )
    from docker_mcp.services import ConfigService, ContainerService, HostService, StackService
    from docker_mcp.tools.logs import LogTools


class DockerMCPServer:
    """FastMCP server for Docker management via Docker contexts."""

    def __init__(self, config: DockerMCPConfig, config_path: str | None = None):
        self.config = config
        self._config_path: str = config_path or os.getenv("DOCKER_HOSTS_CONFIG") or "config/hosts.yml"
        
        # Setup dual logging system first (before any logging)
        setup_logging(
            log_dir=os.getenv("LOG_DIR", "logs"),
            log_level=os.getenv("LOG_LEVEL"),
            max_file_size_mb=int(os.getenv("LOG_FILE_SIZE_MB", "10"))
        )
        
        # Use server logger (writes to mcp_server.log)
        self.logger = get_server_logger()

        # Initialize core managers
        self.context_manager = DockerContextManager(config)

        # Initialize service layer
        self.host_service = HostService(config)
        self.container_service = ContainerService(config, self.context_manager)
        self.stack_service = StackService(config, self.context_manager)
        self.config_service = ConfigService(config, self.context_manager)

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

        # Register tools using the correct FastMCP pattern
        # Host management tools
        self.app.tool(self.add_docker_host)
        self.app.tool(self.list_docker_hosts)

        # Container management tools
        self.app.tool(self.list_containers)
        self.app.tool(self.get_container_info)
        self.app.tool(self.manage_container)  # Unified container actions
        self.app.tool(self.list_host_ports)  # Port listing and conflict detection

        # Stack management tools
        self.app.tool(self.deploy_stack)
        self.app.tool(self.manage_stack)  # Unified stack management
        self.app.tool(self.list_stacks)

        # Log management tools
        self.app.tool(self.get_container_logs)

        # Configuration management tools
        self.app.tool(self.update_host_config)
        self.app.tool(self.discover_compose_paths)
        self.app.tool(self.import_ssh_config)

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
    ) -> dict[str, Any]:
        """Add a new Docker host for management."""
        return await self.host_service.add_docker_host(
            host_id, ssh_host, ssh_user, ssh_port, ssh_key_path, description, tags, test_connection
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

    # Setup logging
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    logger = structlog.get_logger()
    logger.setLevel(args.log_level)

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
