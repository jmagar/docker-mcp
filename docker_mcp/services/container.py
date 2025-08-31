"""
Container Management Service

Business logic for Docker container operations with formatted output.
"""

from typing import Any

import structlog
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from ..constants import CONTAINER_ID, HOST_ID
from ..core.config_loader import DockerMCPConfig
from ..core.docker_context import DockerContextManager
from ..tools.containers import ContainerTools
from ..utils import validate_host


class ContainerService:
    """Service for Docker container management operations."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager
        self.cache_manager = None
        self.container_tools = ContainerTools(config, context_manager)
        self.logger = structlog.get_logger()

    def set_cache_manager(self, cache_manager):
        """Set the cache manager after initialization."""
        self.cache_manager = cache_manager

    async def _list_containers_cached(
        self, host_id: str, all_containers: bool = False, limit: int = 20, offset: int = 0
    ) -> dict:
        """List containers using cache manager."""
        try:
            # Get containers from cache
            cached_containers = await self.cache_manager.get_containers(host_id)

            # Filter by status if not showing all containers
            if not all_containers:
                # Only show running containers
                filtered_containers = [
                    c for c in cached_containers if c.status.lower() in ["running", "up"]
                ]
            else:
                filtered_containers = cached_containers

            # Convert cache objects to dict format using optimized method
            containers = [
                cached_container.to_service_dict() for cached_container in filtered_containers
            ]

            # Apply pagination
            total = len(containers)
            start_idx = offset
            end_idx = min(offset + limit, total)
            paginated_containers = containers[start_idx:end_idx]

            # Build pagination info
            pagination = {
                "total": total,
                "returned": len(paginated_containers),
                "offset": offset,
                "limit": limit,
                "has_next": end_idx < total,
            }

            self.logger.info(
                "Listed containers from cache",
                host_id=host_id,
                total_containers=total,
                returned=len(paginated_containers),
                all_containers=all_containers,
            )

            return {
                "success": True,
                HOST_ID: host_id,
                "containers": paginated_containers,
                "pagination": pagination,
            }

        except Exception as e:
            self.logger.error("Failed to list containers from cache", host_id=host_id, error=str(e))
            # Fall back to container tools
            return await self.container_tools.list_containers(
                host_id, all_containers, limit, offset
            )

    async def _get_container_info_cached(self, host_id: str, container_id: str) -> dict:
        """Get container info using cache manager."""
        try:
            # Get specific container from cache
            cached_container = await self.cache_manager.get_container(host_id, container_id)

            if not cached_container:
                return {"error": f"Container '{container_id}' not found on host '{host_id}'"}

            # Convert cache object to dict format expected by the service
            container_info = {
                "success": True,
                HOST_ID: host_id,
                "container": {
                    "id": cached_container.container_id,
                    "name": cached_container.name,
                    "image": cached_container.image,
                    "status": cached_container.status,
                    "state": "running"
                    if cached_container.status.lower() in ["running", "up"]
                    else "stopped",
                    "created": cached_container.created,
                    "started": cached_container.started,
                    "ports": cached_container.ports,
                    "labels": cached_container.labels,
                    "environment": {},  # Removed from cache for memory optimization
                    "mounts": {
                        "bind_mounts": cached_container.bind_mounts,
                        "volumes": cached_container.volumes,
                        "volume_drivers": cached_container.volume_drivers,
                    },
                    "networks": cached_container.network_aliases,
                    "compose_project": cached_container.compose_project,
                    "compose_service": cached_container.compose_service,
                    "compose_config_files": cached_container.compose_config_files,
                    "compose_working_dir": cached_container.compose_working_dir,
                    "health_status": cached_container.health_status,
                    "restart_policy": cached_container.restart_policy,
                    "cpu_usage": cached_container.cpu_usage,
                    "memory_usage": cached_container.memory_usage,
                    "memory_limit": cached_container.memory_limit,
                    "log_tail": [],  # Removed from cache for memory optimization
                    "working_dir": cached_container.working_dir,
                },
            }

            self.logger.info(
                "Retrieved container info from cache",
                host_id=host_id,
                container_id=container_id,
                container_name=cached_container.name,
            )

            return container_info

        except Exception as e:
            self.logger.error(
                "Failed to get container info from cache",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            # Fall back to container tools
            return await self.container_tools.get_container_info(host_id, container_id)

    def _validate_container_safety(self, container_id: str) -> tuple[bool, str]:
        """Validate container is safe for testing operations."""
        # List of production containers that should not be operated on during tests
        production_containers = {
            "opengist",
            "nextcloud",
            "plex",
            "portainer",
            "traefik",
            "mysql",
            "postgres",
            "redis",
            "mongodb",
            "elasticsearch",
            "grafana",
            "prometheus",
            "nginx-proxy",
            "ssl-companion",
        }

        # Check if this looks like a production container
        if container_id.lower() in production_containers:
            return (
                False,
                f"Safety check failed: '{container_id}' appears to be a production container. Use test containers for testing.",
            )

        # Allow test containers (those with "test" prefix or specific test patterns)
        if (
            container_id.startswith("test-")
            or "test" in container_id.lower()
            or container_id.startswith("mcp-")
        ):
            return True, ""

        # For other containers, issue a warning but allow operation
        # This preserves backward compatibility while encouraging safe practices
        self.logger.warning(
            "Operating on container that may be production",
            container_id=container_id,
            recommendation="Use test containers (test-*) for safer testing",
        )
        return True, ""

    async def list_containers(
        self, host_id: str, all_containers: bool = False, limit: int = 20, offset: int = 0
    ) -> ToolResult:
        """List containers on a specific Docker host with pagination."""
        try:
            is_valid, error_msg = validate_host(self.config, host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use cache manager if available, otherwise fall back to container tools
            if self.cache_manager:
                result = await self._list_containers_cached(host_id, all_containers, limit, offset)
            else:
                # Use container tools to get containers with pagination
                result = await self.container_tools.list_containers(
                    host_id, all_containers, limit, offset
                )

            # Create clean, professional summary
            containers = result["containers"]
            pagination = result["pagination"]

            summary_lines = [
                f"Docker Containers on {host_id}",
                f"Showing {pagination['returned']} of {pagination['total']} containers",
                "",
            ]

            for container in containers:
                summary_lines.extend(self._format_container_summary(container))

            if pagination["has_next"]:
                summary_lines.append(
                    f"\nNext page: Use offset={pagination['offset'] + pagination['limit']}"
                )

            return ToolResult(
                content=[TextContent(type="text", text="\n".join(summary_lines))],
                structured_content={
                    "success": True,
                    HOST_ID: host_id,
                    "containers": containers,
                    "pagination": pagination,
                },
            )

        except Exception as e:
            self.logger.error("Failed to list containers", host_id=host_id, error=str(e))
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to list containers: {str(e)}")],
                structured_content={"success": False, "error": str(e), HOST_ID: host_id},
            )

    def _format_container_summary(self, container: dict[str, Any]) -> list[str]:
        """Format container information for display."""
        status_indicator = "●" if container["state"] == "running" else "○"
        ports_info = f" | Ports: {', '.join(container['ports'])}" if container["ports"] else ""

        # Show volume and network info if available
        volume_info = (
            f" | Volumes: {len(container.get('volumes', []))}" if container.get("volumes") else ""
        )
        network_info = (
            f" | Networks: {', '.join(container.get('networks', []))}"
            if container.get("networks")
            else ""
        )
        compose_info = (
            f" | Project: {container.get('compose_project')}"
            if container.get("compose_project")
            else ""
        )

        return [
            f"{status_indicator} {container['name']} ({container['id']})\n"
            f"    Image: {container['image']}\n"
            f"    Status: {container['status']}{ports_info}{volume_info}{network_info}{compose_info}"
        ]

    async def get_container_info(self, host_id: str, container_id: str) -> ToolResult:
        """Get detailed information about a specific container."""
        try:
            is_valid, error_msg = validate_host(self.config, host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use cache manager if available, otherwise fall back to container tools
            if self.cache_manager:
                container_info = await self._get_container_info_cached(host_id, container_id)
            else:
                # Use container tools to get container info
                container_info = await self.container_tools.get_container_info(
                    host_id, container_id
                )

            if "error" in container_info:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {container_info['error']}")],
                    structured_content={
                        "success": False,
                        "error": container_info["error"],
                        HOST_ID: host_id,
                        CONTAINER_ID: container_id,
                    },
                )

            summary_lines = self._format_container_details(container_info, container_id)

            return ToolResult(
                content=[TextContent(type="text", text="\n".join(summary_lines))],
                structured_content={
                    "success": True,
                    HOST_ID: host_id,
                    CONTAINER_ID: container_id,
                    "info": container_info,
                },
            )

        except Exception as e:
            self.logger.error(
                "Failed to get container info",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return ToolResult(
                content=[
                    TextContent(type="text", text=f"❌ Failed to get container info: {str(e)}")
                ],
                structured_content={
                    "success": False,
                    "error": str(e),
                    HOST_ID: host_id,
                    CONTAINER_ID: container_id,
                },
            )

    def _format_container_details(
        self, container_info: dict[str, Any], container_id: str
    ) -> list[str]:
        """Format detailed container information for display."""
        name = container_info.get("name", container_id)
        status = container_info.get("status", "unknown")
        image = container_info.get("image", "unknown")

        summary_lines = [
            f"Container: {name} ({container_id[:12]})",
            f"Image: {image}",
            f"Status: {status}",
            "",
        ]

        # Add volume information
        volumes = container_info.get("volumes", [])
        if volumes:
            summary_lines.append("Volume Mounts:")
            for volume in volumes[:10]:  # Show up to 10
                summary_lines.append(f"  {volume}")
            if len(volumes) > 10:
                summary_lines.append(f"  ... and {len(volumes) - 10} more volumes")
            summary_lines.append("")

        # Add network information
        networks = container_info.get("networks", [])
        if networks:
            summary_lines.append(f"Networks: {', '.join(networks)}")
            summary_lines.append("")

        # Add compose information
        compose_project = container_info.get("compose_project", "")
        if compose_project:
            summary_lines.append(f"Compose Project: {compose_project}")
            compose_file = container_info.get("compose_file", "")
            if compose_file:
                summary_lines.append(f"Compose File: {compose_file}")
            summary_lines.append("")

        # Add port information
        ports = container_info.get("ports", {})
        if ports:
            summary_lines.extend(self._format_port_mappings(ports))

        return summary_lines

    def _format_port_mappings(self, ports: dict[str, Any]) -> list[str]:
        """Format port mappings for display."""
        lines = ["Port Mappings:"]
        for container_port, host_mappings in ports.items():
            if host_mappings:
                for mapping in host_mappings:
                    host_ip = mapping.get("HostIp", "0.0.0.0")  # nosec B104 - Docker port mapping
                    host_port = mapping.get("HostPort", "")
                    lines.append(f"  {host_ip}:{host_port} -> {container_port}")
            else:
                lines.append(f"  {container_port} (not exposed)")
        return lines

    async def manage_container(
        self, host_id: str, container_id: str, action: str, force: bool = False, timeout: int = 10
    ) -> ToolResult:
        """Unified container action management."""
        try:
            is_valid, error_msg = validate_host(self.config, host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Safety check for production containers
            is_safe, safety_msg = self._validate_container_safety(container_id)
            if not is_safe:
                self.logger.warning(
                    "Container operation blocked by safety check",
                    host_id=host_id,
                    container_id=container_id,
                    action=action,
                    reason=safety_msg,
                )
                return ToolResult(
                    content=[TextContent(type="text", text=f"⚠️  {safety_msg}")],
                    structured_content={
                        "success": False,
                        "error": safety_msg,
                        "safety_blocked": True,
                    },
                )

            # Use container tools to manage container
            result = await self.container_tools.manage_container(
                host_id, container_id, action, force, timeout
            )

            if result["success"]:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Success: {result['message']}")],
                    structured_content=result,
                )
            else:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {result['message']}")],
                    structured_content=result,
                )

        except Exception as e:
            self.logger.error(
                "Failed to manage container",
                host_id=host_id,
                container_id=container_id,
                action=action,
                error=str(e),
            )
            return ToolResult(
                content=[
                    TextContent(type="text", text=f"❌ Failed to {action} container: {str(e)}")
                ],
                structured_content={
                    "success": False,
                    "error": str(e),
                    HOST_ID: host_id,
                    CONTAINER_ID: container_id,
                    "action": action,
                },
            )

    async def pull_image(self, host_id: str, image_name: str) -> ToolResult:
        """Pull a Docker image on a remote host."""
        try:
            is_valid, error_msg = validate_host(self.config, host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use container tools to pull image
            result = await self.container_tools.pull_image(host_id, image_name)

            if result["success"]:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Success: {result['message']}")],
                    structured_content=result,
                )
            else:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {result['message']}")],
                    structured_content=result,
                )

        except Exception as e:
            self.logger.error(
                "Failed to pull image",
                host_id=host_id,
                image_name=image_name,
                error=str(e),
            )
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to pull image: {str(e)}")],
                structured_content={
                    "success": False,
                    "error": str(e),
                    HOST_ID: host_id,
                    "image_name": image_name,
                },
            )

    async def list_host_ports(self, host_id: str) -> ToolResult:
        """List all ports currently in use by containers on a Docker host (includes stopped containers)."""
        try:
            is_valid, error_msg = validate_host(self.config, host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use container tools to get port information (always include stopped containers)
            result = await self.container_tools.list_host_ports(host_id)

            summary_lines = self._format_port_usage_summary(result, host_id)

            return ToolResult(
                content=[TextContent(type="text", text="\n".join(summary_lines))],
                structured_content={
                    "success": True,
                    HOST_ID: host_id,
                    "total_ports": result["total_ports"],
                    "total_containers": result["total_containers"],
                    "port_mappings": result["port_mappings"],
                    "conflicts": result["conflicts"],
                    "summary": result["summary"],
                    "cached": result.get("cached", False),
                    "timestamp": result.get("timestamp"),
                },
            )

        except Exception as e:
            self.logger.error("Failed to list host ports", host_id=host_id, error=str(e))
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to list host ports: {str(e)}")],
                structured_content={"success": False, "error": str(e), HOST_ID: host_id},
            )

    def _format_port_usage_summary(self, result: dict[str, Any], host_id: str) -> list[str]:
        """Format comprehensive port usage summary."""
        port_mappings = result["port_mappings"]
        conflicts = result["conflicts"]
        summary = result["summary"]

        summary_lines = [
            f"Port Usage on {host_id}",
            f"Found {result['total_ports']} exposed ports across {result['total_containers']} containers",
            "",
        ]

        # Show summary statistics
        if summary.get("protocol_counts"):
            protocol_info = ", ".join(
                [f"{protocol}: {count}" for protocol, count in summary["protocol_counts"].items()]
            )
            summary_lines.append(f"Protocols: {protocol_info}")

        if summary.get("port_range_usage"):
            ranges = summary["port_range_usage"]
            range_info = f"System: {ranges.get('0-1023', 0)}, User: {ranges.get('1024-49151', 0)}, Dynamic: {ranges.get('49152-65535', 0)}"
            summary_lines.append(f"Port ranges: {range_info}")

        if conflicts:
            summary_lines.append(f"⚠️  {len(conflicts)} port conflicts detected!")

        summary_lines.append("")

        # Show port conflicts first (if any)
        if conflicts:
            summary_lines.extend(self._format_port_conflicts(conflicts))

        # Show all port mappings
        if port_mappings:
            summary_lines.extend(self._format_port_mapping_details(port_mappings))
        else:
            summary_lines.append("No exposed ports found.")

        # Add helpful notes
        if conflicts:
            summary_lines.extend(
                [
                    "",
                    "Note: Port conflicts occur when multiple containers try to bind to the same host port.",
                    "Only one container can successfully bind - others may fail to start or function incorrectly.",
                ]
            )

        return summary_lines

    def _format_port_conflicts(self, conflicts: list[dict[str, Any]]) -> list[str]:
        """Format port conflict information."""
        lines = ["PORT CONFLICTS:"]
        for conflict in conflicts:
            host_port = conflict["host_port"]
            protocol = conflict["protocol"]
            host_ip = conflict["host_ip"]
            containers = conflict["affected_containers"]

            lines.append(f"❌ {host_ip}:{host_port}/{protocol} used by: {', '.join(containers)}")
        lines.append("")
        return lines

    def _format_port_mapping_details(self, port_mappings: list[dict[str, Any]]) -> list[str]:
        """Format detailed port mapping information."""
        lines = ["PORT MAPPINGS:"]

        # Sort by host port for better readability
        sorted_mappings = sorted(
            port_mappings,
            key=lambda x: (
                x["host_ip"],
                int(x["host_port"]) if x["host_port"].isdigit() else 0,
            ),
        )

        for mapping in sorted_mappings:
            conflict_indicator = "⚠️ " if mapping["is_conflict"] else "  "
            host_mapping = f"{mapping['host_ip']}:{mapping['host_port']}"
            container_mapping = f"{mapping['container_port']}/{mapping['protocol']}"
            container_info = f"{mapping['container_name']} ({mapping['container_id']})"

            lines.append(
                f"{conflict_indicator}{host_mapping} → {container_mapping} | {container_info}"
            )

            # Show image and compose project if available
            details = []
            if mapping.get("image"):
                details.append(f"Image: {mapping['image']}")
            if mapping.get("compose_project"):
                details.append(f"Project: {mapping['compose_project']}")

            if details:
                lines.append(f"    {' | '.join(details)}")

            # Show conflict details
            if mapping["is_conflict"] and mapping.get("conflict_with"):
                conflict_with = ", ".join(mapping["conflict_with"])
                lines.append(f"    Conflicts with: {conflict_with}")

        return lines

    async def check_port_availability(self, host_id: str, port: int) -> dict[str, Any]:
        """Check if a specific port is available on a host.

        Args:
            host_id: Host identifier to check
            port: Port number to check

        Returns:
            Port availability information
        """
        try:
            is_valid, error_msg = validate_host(self.config, host_id)
            if not is_valid:
                return {"success": False, "error": error_msg}

            # Get current port usage (always include stopped containers)
            result = await self.container_tools.list_host_ports(host_id)

            if "error" in result:
                return {"success": False, "error": result["error"]}

            # Check if the specific port is in use
            port_mappings = result.get("port_mappings", [])

            conflicts = []
            for mapping in port_mappings:
                if mapping.get("host_port") == str(port):
                    conflicts.append(
                        {
                            "container_name": mapping.get("container_name"),
                            CONTAINER_ID: mapping.get(CONTAINER_ID),
                            "image": mapping.get("image"),
                            "protocol": mapping.get("protocol", "tcp"),
                        }
                    )

            is_available = len(conflicts) == 0

            return {
                "success": True,
                HOST_ID: host_id,
                "port": port,
                "available": is_available,
                "conflicts": conflicts,
                "message": f"Port {port} is {'available' if is_available else 'in use'}",
            }

        except Exception as e:
            self.logger.error(
                "Failed to check port availability", host_id=host_id, port=port, error=str(e)
            )
            return {
                "success": False,
                "error": f"Port check failed: {str(e)}",
                HOST_ID: host_id,
                "port": port,
            }

    async def handle_action(self, action, **params) -> dict[str, Any]:
        """Unified action handler for all container operations.

        This method consolidates all dispatcher logic from server.py into the service layer.
        """
        try:
            # Import dependencies for this handler
            from ..models.enums import ContainerAction

            # Extract common parameters
            host_id = params.get("host_id", "")
            container_id = params.get("container_id", "")
            all_containers = params.get("all_containers", False)
            limit = params.get("limit", 20)
            offset = params.get("offset", 0)
            follow = params.get("follow", False)
            lines = params.get("lines", 100)
            force = params.get("force", False)
            timeout = params.get("timeout", 10)

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

                # TODO: Move logs functionality to service layer
                # For now, delegate to tools layer
                try:
                    from ..tools.logs import LogTools

                    log_tools = LogTools(self.config, self.context_manager)
                    logs_result = await log_tools.get_container_logs(
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

            else:
                return {
                    "success": False,
                    "error": f"Unknown action: {action}",
                    "valid_actions": [
                        "list",
                        "info",
                        "start",
                        "stop",
                        "restart",
                        "build",
                        "logs",
                        "pull",
                    ],
                }

        except Exception as e:
            self.logger.error("container service action error", action=action, error=str(e))
            return {"success": False, "error": f"Service action failed: {str(e)}", "action": action}
