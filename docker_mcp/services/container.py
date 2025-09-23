"""
Container Management Service

Business logic for Docker container operations with formatted output.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docker_mcp.core.docker_context import DockerContextManager

import structlog
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from ..constants import CONTAINER_ID, HOST_ID
from ..core.config_loader import DockerMCPConfig
from ..tools.containers import ContainerTools
from ..utils import validate_host
from .logs import LogsService


class ContainerService:
    """Service for Docker container management operations."""

    def __init__(
        self,
        config: DockerMCPConfig,
        context_manager: "DockerContextManager",
        logs_service: LogsService | None = None,
    ):
        self.config = config
        self.context_manager = context_manager
        self.container_tools = ContainerTools(config, context_manager)
        self.logs_service = logs_service or LogsService(config, context_manager)
        self.logger = structlog.get_logger()

    def _build_error_response(
        self,
        host_id: str,
        container_id: str | None,
        action: str | None,
        error: Exception,
        message: str,
    ) -> dict[str, Any]:
        """Build a standardized error response."""
        self.logger.error(
            "container service error",
            host_id=host_id,
            container_id=container_id,
            action=action,
            error=str(error),
            error_type=type(error).__name__,
        )
        return {
            "success": False,
            "message": message,
            "host_id": host_id,
            "container_id": container_id,
            "action": action,
            "error": str(error),
            "error_type": type(error).__name__,
            "timestamp": datetime.now(UTC).isoformat(),
        }

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

    async def _check_container_exists(self, host_id: str, container_id: str) -> dict[str, Any]:
        """Check if a container exists on the host before performing operations."""
        try:
            # Use container tools to get container info (which checks existence)
            container_info = await self.container_tools.get_container_info(host_id, container_id)
            
            if "error" in container_info:
                # Try to provide helpful suggestions
                suggestion = ""
                if "not found" in container_info["error"].lower():
                    # Get list of available containers to suggest alternatives
                    containers_result = await self.container_tools.list_containers(host_id, all_containers=True, limit=10, offset=0)
                    if containers_result.get("success") and containers_result.get("containers"):
                        container_names = [c.get("name", "") for c in containers_result["containers"]]
                        # Find similar names
                        similar_names = [name for name in container_names if container_id.lower() in name.lower() or name.lower() in container_id.lower()]
                        if similar_names:
                            suggestion = f"Did you mean one of: {', '.join(similar_names[:3])}?"
                        else:
                            suggestion = f"Available containers: {', '.join(container_names[:5])}"
                
                return {
                    "exists": False,
                    "error": container_info["error"],
                    "suggestion": suggestion
                }
            
            return {"exists": True, "info": container_info}
            
        except Exception as e:
            return {
                "exists": False,
                "error": f"Failed to check container existence: {str(e)}",
                "suggestion": "Verify the container name and try again"
            }

    def _enhance_operation_result(self, result: dict[str, Any], host_id: str, container_id: str, action: str) -> dict[str, Any]:
        """Enhance operation result with context and user-friendly messaging."""
        from datetime import UTC, datetime
        
        enhanced = result.copy()
        
        # Add operation context
        enhanced["operation_context"] = {
            "host_id": host_id,
            "container_id": container_id,
            "action": action,
            "timestamp": datetime.now(UTC).isoformat(),
            "operation_type": "container_management"
        }
        
        # Create user-friendly messages
        if result.get("success"):
            action_messages = {
                "start": f"Container '{container_id}' started successfully on {host_id}",
                "stop": f"Container '{container_id}' stopped successfully on {host_id}",
                "restart": f"Container '{container_id}' restarted successfully on {host_id}",
                "remove": f"Container '{container_id}' removed successfully from {host_id}",
                "pause": f"Container '{container_id}' paused successfully on {host_id}",
                "unpause": f"Container '{container_id}' unpaused successfully on {host_id}"
            }
            enhanced["user_message"] = action_messages.get(action, f"Container operation '{action}' completed successfully")
            
            # Add helpful next steps
            if action == "start":
                enhanced["next_steps"] = [
                    "Check container logs if needed: docker_container logs",
                    "Monitor container status: docker_container info"
                ]
            elif action == "stop":
                enhanced["next_steps"] = [
                    "Container can be restarted with: docker_container start",
                    "Remove container if no longer needed: docker_container remove"
                ]
        else:
            error_message = result.get("message", result.get("error", "Unknown error"))
            enhanced["user_message"] = f"Failed to {action} container '{container_id}' on {host_id}: {error_message}"
            
            # Add troubleshooting hints
            enhanced["troubleshooting_hints"] = [
                "Verify the container name is correct",
                "Check if you have sufficient permissions",
                "Ensure the container is in the correct state for this operation"
            ]
            
            if "permission denied" in error_message.lower():
                enhanced["troubleshooting_hints"].insert(0, "Check Docker daemon permissions and user group membership")
            elif "already" in error_message.lower():
                enhanced["troubleshooting_hints"].insert(0, "Container may already be in the target state")
        
        # Preserve original message in raw_message for debugging
        enhanced["raw_message"] = result.get("message", "")
        
        return enhanced

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
                f"{'':1} {'Container':<25} {'Ports':<20} {'Project':<15}",
                f"{'':1} {'-' * 25:<25} {'-' * 20:<20} {'-' * 15:<15}",
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
        """Format container information for display - compact single line format."""
        status_indicator = "●" if container["state"] == "running" else "○"

        # Extract first 3 host ports for compact display
        ports = container.get("ports", [])
        if ports:
            # Extract host ports from format like "0.0.0.0:8012→8000/tcp"
            host_ports = []
            for port in ports[:3]:  # Show max 3 ports
                if ":" in port and "→" in port:
                    host_port = port.split(":")[1].split("→")[0]
                    host_ports.append(host_port)
            ports_display = ",".join(host_ports)
            if len(ports) > 3:
                ports_display += f" +{len(ports) - 3} more"
        else:
            ports_display = "-"

        # Truncate names for alignment
        name = container["name"][:25]
        project = container.get("compose_project", "-")[:15]

        # Safe formatting without alignment to debug format string error
        return [f"{status_indicator} {name} | {ports_display} | {project}"]

    async def get_container_info(self, host_id: str, container_id: str) -> ToolResult:
        """Get detailed information about a specific container."""
        try:
            is_valid, error_msg = validate_host(self.config, host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use container tools to get container info
            container_info = await self.container_tools.get_container_info(host_id, container_id)

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

            # Enhance response with operation context and user-friendly formatting
            enhanced_result = self._enhance_operation_result(result, host_id, container_id, action)
            
            if enhanced_result["success"]:
                return ToolResult(
                    content=[TextContent(type="text", text=f"✅ {enhanced_result['user_message']}")],
                    structured_content=enhanced_result,
                )
            else:
                return ToolResult(
                    content=[TextContent(type="text", text=f"❌ {enhanced_result['user_message']}")],
                    structured_content=enhanced_result,
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
        """Format port mapping information grouped by container for efficiency."""
        if not port_mappings:
            return ["No exposed ports found."]

        lines = ["PORT MAPPINGS:"]

        # Group ports by container for efficient display
        by_container = {}
        conflicts_found = []

        for mapping in port_mappings:
            container_key = mapping.get("container_name", "unknown")
            if container_key not in by_container:
                by_container[container_key] = {
                    "ports": [],
                    "compose_project": mapping.get("compose_project", ""),
                    "container_id": mapping.get("container_id", ""),
                }

            # Format: host_port→container_port/protocol using safe defaults
            host_port = mapping.get("host_port", "")
            container_port = mapping.get("container_port", "")
            protocol = mapping.get("protocol", "")
            port_str = f"{host_port}→{container_port}/{protocol}"

            if mapping.get("is_conflict", False):
                port_str = f"⚠️{port_str}"
                conflicts_found.append(f"{host_port}/{protocol}")

            by_container[container_key]["ports"].append(port_str)

        # Display grouped by container
        for container_name, container_data in sorted(by_container.items()):
            ports_str = ", ".join(container_data["ports"])
            project_info = (
                f" [{container_data['compose_project']}]"
                if container_data["compose_project"]
                else ""
            )
            lines.append(f"  {container_name}{project_info}: {ports_str}")

        # Add conflicts summary if any
        if conflicts_found:
            lines.append("")
            lines.append(f"⚠️  Conflicts detected on ports: {', '.join(conflicts_found)}")

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

            # Route to appropriate handler
            if action == ContainerAction.LIST:
                return await self._handle_list_action(host_id, all_containers, limit, offset)
            elif action == ContainerAction.INFO:
                return await self._handle_info_action(host_id, container_id)
            elif action in [
                ContainerAction.START,
                ContainerAction.STOP,
                ContainerAction.RESTART,
                ContainerAction.REMOVE,
            ]:
                return await self._handle_management_actions(
                    action, host_id, container_id, force, timeout
                )
            elif action == ContainerAction.LOGS:
                return await self._handle_logs_action(host_id, container_id, lines, follow)
            elif action == "pull":
                return await self._handle_pull_action(host_id, container_id)
            else:
                return self._handle_unknown_action(action)

        except Exception as e:
            self.logger.error("container service action error", action=action, error=str(e))
            return {"success": False, "error": f"Service action failed: {str(e)}", "action": action}

    async def _handle_list_action(
        self, host_id: str, all_containers: bool, limit: int, offset: int
    ) -> dict[str, Any]:
        """Handle list container action."""
        if not host_id:
            return self._build_error_response(
                host_id="",
                container_id=None,
                action="list",
                error=ValueError("host_id missing"),
                message="host_id is required for list action",
            )

        # Validate pagination parameters
        if limit < 1 or limit > 1000:
            return self._build_error_response(
                host_id=host_id,
                container_id=None,
                action="list",
                error=ValueError("invalid limit"),
                message="limit must be between 1 and 1000",
            )
        if offset < 0:
            return self._build_error_response(
                host_id=host_id,
                container_id=None,
                action="list",
                error=ValueError("invalid offset"),
                message="offset must be >= 0",
            )

        result = await self.list_containers(host_id, all_containers, limit, offset)
        return self._extract_structured_content(result)

    async def _handle_info_action(self, host_id: str, container_id: str) -> dict[str, Any]:
        """Handle container info action."""
        if not host_id:
            return self._build_error_response(
                host_id="",
                container_id=None,
                action="info",
                error=ValueError("host_id missing"),
                message="host_id is required for info action",
            )
        if not container_id:
            return self._build_error_response(
                host_id=host_id,
                container_id=None,
                action="info",
                error=ValueError("container_id missing"),
                message="container_id is required for info action",
            )

        info_result = await self.get_container_info(host_id, container_id)
        return self._extract_structured_content(info_result)

    async def _handle_management_actions(
        self, action, host_id: str, container_id: str, force: bool, timeout: int
    ) -> dict[str, Any]:
        """Handle container management actions (start, stop, restart, etc.)."""
        if not host_id:
            return self._build_error_response(
                host_id="",
                container_id=None,
                action=str(action),
                error=ValueError("host_id missing"),
                message=f"host_id is required for {action} action",
            )
        if not container_id:
            return self._build_error_response(
                host_id=host_id,
                container_id=None,
                action=str(action),
                error=ValueError("container_id missing"),
                message=f"container_id is required for {action} action",
            )

        # Validate timeout parameter
        if timeout < 1 or timeout > 300:
            return self._build_error_response(
                host_id=host_id,
                container_id=container_id,
                action=str(action),
                error=ValueError("invalid timeout"),
                message="timeout must be between 1 and 300 seconds",
            )

        result = await self.manage_container(host_id, container_id, action.value, force, timeout)
        return self._extract_structured_content(result)

    async def _handle_logs_action(
        self, host_id: str, container_id: str, lines: int, follow: bool
    ) -> dict[str, Any]:
        """Handle container logs action."""
        if not host_id:
            return self._build_error_response(
                host_id="",
                container_id=None,
                action="logs",
                error=ValueError("host_id missing"),
                message="host_id is required for logs action",
            )
        if not container_id:
            return self._build_error_response(
                host_id=host_id,
                container_id=None,
                action="logs",
                error=ValueError("container_id missing"),
                message="container_id is required for logs action",
            )

        # Validate lines parameter
        if lines < 1 or lines > 10000:
            return self._build_error_response(
                host_id=host_id,
                container_id=container_id,
                action="logs",
                error=ValueError("invalid lines parameter"),
                message="lines must be between 1 and 10000",
            )

        try:
            logs_result = await self.logs_service.get_container_logs(
                host_id=host_id,
                container_id=container_id,
                lines=lines,
                since=None,
                timestamps=False,
            )

            # Extract logs array from ContainerLogs model for cleaner API
            if isinstance(logs_result, dict) and "logs" in logs_result:
                logs = logs_result["logs"]
                truncated = logs_result.get("truncated", False)
            else:
                logs = []
                truncated = False

            return {
                "success": True,
                "host_id": host_id,
                "container_id": container_id,
                "logs": logs,
                "lines_requested": lines,
                "lines_returned": len(logs),
                "truncated": truncated,
                "follow": follow,
            }

        except Exception as e:
            return self._build_error_response(
                host_id=host_id,
                container_id=container_id,
                action="logs",
                error=e,
                message="Failed to get container logs",
            )

    async def _handle_pull_action(self, host_id: str, container_id: str) -> dict[str, Any]:
        """Handle image pull action."""
        if not host_id:
            return self._build_error_response(
                host_id="",
                container_id=None,
                action="pull",
                error=ValueError("host_id missing"),
                message="host_id is required for pull action",
            )
        if not container_id:
            return self._build_error_response(
                host_id=host_id,
                container_id=None,
                action="pull",
                error=ValueError("image name missing"),
                message="container_id is required for pull action (image name)",
            )

        # For pull, container_id is actually the image name
        result = await self.pull_image(host_id, container_id)
        return self._extract_structured_content(result)

    def _handle_unknown_action(self, action) -> dict[str, Any]:
        """Handle unknown action."""
        return {
            "success": False,
            "error": f"Unknown action: {action}",
            "valid_actions": [
                "list",
                "info",
                "start",
                "stop",
                "restart",
                "logs",
                "pull",
            ],
        }

    def _extract_structured_content(self, result) -> dict[str, Any]:
        """Extract structured content from ToolResult."""
        return (
            result.structured_content
            if hasattr(result, "structured_content") and result.structured_content is not None
            else {"success": False, "error": "Invalid result format"}
        )
