"""
Container Management Service

Business logic for Docker container operations with formatted output.
"""

from typing import Any

import structlog
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from ..core.config import DockerMCPConfig
from ..core.docker_context import DockerContextManager
from ..tools.containers import ContainerTools


class ContainerService:
    """Service for Docker container management operations."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager
        self.container_tools = ContainerTools(config, context_manager)
        self.logger = structlog.get_logger()

    def _validate_host(self, host_id: str) -> tuple[bool, str]:
        """Validate host exists in configuration."""
        if host_id not in self.config.hosts:
            return False, f"Host '{host_id}' not found"
        return True, ""

    def _validate_container_safety(self, container_id: str) -> tuple[bool, str]:
        """Validate container is safe for testing operations."""
        # List of production containers that should not be operated on during tests
        production_containers = {
            "opengist", "nextcloud", "plex", "portainer", "traefik", 
            "mysql", "postgres", "redis", "mongodb", "elasticsearch",
            "grafana", "prometheus", "nginx-proxy", "ssl-companion"
        }
        
        # Check if this looks like a production container
        if container_id.lower() in production_containers:
            return False, f"Safety check failed: '{container_id}' appears to be a production container. Use test containers for testing."
        
        # Allow test containers (those with "test" prefix or specific test patterns)
        if (container_id.startswith("test-") or 
            "test" in container_id.lower() or 
            container_id.startswith("mcp-")):
            return True, ""
            
        # For other containers, issue a warning but allow operation
        # This preserves backward compatibility while encouraging safe practices
        self.logger.warning(
            "Operating on container that may be production",
            container_id=container_id,
            recommendation="Use test containers (test-*) for safer testing"
        )
        return True, ""

    async def list_containers(
        self, host_id: str, all_containers: bool = False, limit: int = 20, offset: int = 0
    ) -> ToolResult:
        """List containers on a specific Docker host with pagination."""
        try:
            is_valid, error_msg = self._validate_host(host_id)
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
                    "host_id": host_id,
                    "containers": containers,
                    "pagination": pagination,
                },
            )

        except Exception as e:
            self.logger.error("Failed to list containers", host_id=host_id, error=str(e))
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to list containers: {str(e)}")],
                structured_content={"success": False, "error": str(e), "host_id": host_id},
            )

    def _format_container_summary(self, container: dict[str, Any]) -> list[str]:
        """Format container information for display."""
        status_indicator = "●" if container["state"] == "running" else "○"
        ports_info = (
            f" | Ports: {', '.join(container['ports'])}" if container["ports"] else ""
        )

        # Show volume and network info if available
        volume_info = (
            f" | Volumes: {len(container.get('volumes', []))}"
            if container.get("volumes")
            else ""
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
            is_valid, error_msg = self._validate_host(host_id)
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
                        "host_id": host_id,
                        "container_id": container_id,
                    },
                )

            summary_lines = self._format_container_details(container_info, container_id)

            return ToolResult(
                content=[TextContent(type="text", text="\n".join(summary_lines))],
                structured_content={
                    "success": True,
                    "host_id": host_id,
                    "container_id": container_id,
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
                    "host_id": host_id,
                    "container_id": container_id,
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
            is_valid, error_msg = self._validate_host(host_id)
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
                    reason=safety_msg
                )
                return ToolResult(
                    content=[TextContent(type="text", text=f"⚠️  {safety_msg}")],
                    structured_content={"success": False, "error": safety_msg, "safety_blocked": True},
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
                    "host_id": host_id,
                    "container_id": container_id,
                    "action": action,
                },
            )

    async def list_host_ports(self, host_id: str, include_stopped: bool = False) -> ToolResult:
        """List all ports currently in use by containers on a Docker host."""
        try:
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use container tools to get port information
            result = await self.container_tools.list_host_ports(host_id, include_stopped)

            summary_lines = self._format_port_usage_summary(result, host_id)

            return ToolResult(
                content=[TextContent(type="text", text="\n".join(summary_lines))],
                structured_content={
                    "success": True,
                    "host_id": host_id,
                    "total_ports": result["total_ports"],
                    "total_containers": result["total_containers"],
                    "port_mappings": result["port_mappings"],
                    "conflicts": result["conflicts"],
                    "summary": result["summary"],
                },
            )

        except Exception as e:
            self.logger.error("Failed to list host ports", host_id=host_id, error=str(e))
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to list host ports: {str(e)}")],
                structured_content={"success": False, "error": str(e), "host_id": host_id},
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
                [
                    f"{protocol}: {count}"
                    for protocol, count in summary["protocol_counts"].items()
                ]
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
            summary_lines.extend([
                "",
                "Note: Port conflicts occur when multiple containers try to bind to the same host port.",
                "Only one container can successfully bind - others may fail to start or function incorrectly.",
            ])

        return summary_lines

    def _format_port_conflicts(self, conflicts: list[dict[str, Any]]) -> list[str]:
        """Format port conflict information."""
        lines = ["PORT CONFLICTS:"]
        for conflict in conflicts:
            host_port = conflict["host_port"]
            protocol = conflict["protocol"]
            host_ip = conflict["host_ip"]
            containers = conflict["affected_containers"]

            lines.append(
                f"❌ {host_ip}:{host_port}/{protocol} used by: {', '.join(containers)}"
            )
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
