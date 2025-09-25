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
        error_text = str(error) if error else ""
        formatted_message = f"❌ {message}"
        if error_text and error_text not in message:
            formatted_message = f"{formatted_message}\nDetails: {error_text}"
        return {
            "success": False,
            "message": message,
            "host_id": host_id,
            "container_id": container_id,
            "action": action,
            "error": str(error),
            "error_type": type(error).__name__,
            "timestamp": datetime.now(UTC).isoformat(),
            "formatted_output": formatted_message,
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
            container_result = await self.container_tools.get_container_info(host_id, container_id)

            if "error" in container_result:
                # Try to provide helpful suggestions
                suggestion = ""
                if "not found" in container_result["error"].lower():
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
                    "error": container_result["error"],
                    "suggestion": suggestion
                }

            # Container exists, extract info from result
            container_info = container_result.get("info", container_result)
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
            ]

            for container in containers:
                summary_lines.extend(self._format_container_summary(container))

            if pagination["has_next"]:
                summary_lines.append(
                    f"\nNext page: Use offset={pagination['offset'] + pagination['limit']}"
                )

            formatted_text = "\n".join(summary_lines)
            return ToolResult(
                content=[TextContent(type="text", text=formatted_text)],
                structured_content={
                    "success": True,
                    HOST_ID: host_id,
                    "containers": containers,
                    "pagination": pagination,
                    "formatted_output": formatted_text,
                },
            )

        except Exception as e:
            self.logger.error("Failed to list containers", host_id=host_id, error=str(e))
            formatted_text = f"❌ Failed to list containers: {str(e)}"
            return ToolResult(
                content=[TextContent(type="text", text=formatted_text)],
                structured_content={
                    "success": False,
                    "error": str(e),
                    HOST_ID: host_id,
                    "formatted_output": formatted_text,
                },
            )

    def _format_container_summary(self, container: dict[str, Any]) -> list[str]:
        """Format container information for display - enhanced detailed format with full information."""
        # Enhanced status indicators with more states
        state = container.get("state", "unknown")
        status_indicators = {
            "running": "●",
            "exited": "○",
            "stopped": "○",
            "paused": "⏸",
            "restarting": "◐",
            "created": "◯",
            "dead": "✗",
            "removing": "⊗"
        }
        status_indicator = status_indicators.get(state, "?")

        # Show ALL ports without truncation
        ports = container.get("ports", [])
        if ports:
            # Extract all port mappings without data loss
            port_mappings: list[str] = []
            for port in ports:
                if ":" in port and "→" in port:
                    # Extract host port from format like "0.0.0.0:8080→80/tcp"
                    host_part = port.split(":", 1)[1].split("→", 1)[0]
                    container_part = port.split("→", 1)[1] if "→" in port else ""
                    port_mappings.append(f"{host_part}→{container_part}" if container_part else host_part)
                else:
                    # Handle other port formats
                    port_mappings.append(port)
            ports_display = ", ".join(port_mappings)
        else:
            ports_display = "-"

        # Show full names without truncation
        name = container["name"]
        project = container.get("compose_project") or "-"

        # Add network information if available
        networks = container.get("networks", [])
        networks_display = ", ".join(networks) if networks else "-"

        # Enhanced multi-line format for better structure and readability
        container_info = [
            f"{status_indicator} {name}",
            f"    Project: {project}",
            f"    Ports: {ports_display}",
        ]

        # Add networks only if available to avoid clutter
        if networks:
            container_info.append(f"    Networks: {networks_display}")

        return container_info

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
            container_result = await self.container_tools.get_container_info(host_id, container_id)

            if "error" in container_result:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {container_result['error']}")],
                    structured_content={
                        "success": False,
                        "error": container_result["error"],
                        HOST_ID: host_id,
                        CONTAINER_ID: container_id,
                    },
                )

            info_payload = container_result.get("data")
            container_info = info_payload if isinstance(info_payload, dict) else container_result

            summary_lines = self._format_container_details(container_info, container_id)
            formatted_text = "\n".join(summary_lines)

            return ToolResult(
                content=[TextContent(type="text", text=formatted_text)],
                structured_content={
                    "success": True,
                    HOST_ID: host_id,
                    CONTAINER_ID: container_id,
                    "info": container_info,
                    "timestamp": container_result.get("timestamp"),
                    "formatted_output": formatted_text,
                },
            )

        except Exception as e:
            self.logger.error(
                "Failed to get container info",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            formatted_text = f"❌ Failed to get container info: {str(e)}"
            return ToolResult(
                content=[TextContent(type="text", text=formatted_text)],
                structured_content={
                    "success": False,
                    "error": str(e),
                    HOST_ID: host_id,
                    CONTAINER_ID: container_id,
                    "formatted_output": formatted_text,
                },
            )

    def _format_container_info(self, container_info: dict[str, Any], container_id: str) -> list[str]:
        """Format comprehensive container information with ALL details in clean, structured format."""
        name = container_info.get("name", container_id)
        state = container_info.get("state", "unknown")
        image = container_info.get("image", "unknown")
        created = container_info.get("created", "unknown")

        # Enhanced status indicators
        status_indicators = {
            "running": "● Running",
            "exited": "○ Exited",
            "stopped": "○ Stopped",
            "paused": "⏸ Paused",
            "restarting": "◐ Restarting",
            "created": "◯ Created",
            "dead": "✗ Dead",
            "removing": "⊗ Removing"
        }
        status_display = status_indicators.get(state, f"? {state.title()}")

        summary_lines = [
            f"━━━ Container Details: {name} ━━━",
            f"Container ID: {container_id}",
            f"Short ID: {container_id[:12]}",
            f"Status: {status_display}",
            f"Image: {image}",
            f"Created: {created}",
            ""
        ]

        # Runtime information
        runtime_info = []
        if container_info.get("command"):
            runtime_info.append(f"Command: {container_info['command']}")
        if container_info.get("args"):
            runtime_info.append(f"Args: {', '.join(container_info['args'])}")
        if container_info.get("working_dir"):
            runtime_info.append(f"Working Dir: {container_info['working_dir']}")
        if container_info.get("user"):
            runtime_info.append(f"User: {container_info['user']}")

        if runtime_info:
            summary_lines.extend(["Runtime Configuration:"] + [f"  {info}" for info in runtime_info] + [""])

        # Resource limits and usage (if available)
        if container_info.get("memory_limit") or container_info.get("cpu_limit"):
            summary_lines.append("Resource Limits:")
            if container_info.get("memory_limit"):
                summary_lines.append(f"  Memory: {container_info['memory_limit']}")
            if container_info.get("cpu_limit"):
                summary_lines.append(f"  CPU: {container_info['cpu_limit']}")
            summary_lines.append("")

        # Network information - show ALL networks without truncation
        networks = container_info.get("networks", [])
        if networks:
            summary_lines.append("Networks:")
            for network in networks:
                if isinstance(network, dict):
                    network_name = network.get("name", "unknown")
                    network_ip = network.get("ip", "")
                    summary_lines.append(f"  • {network_name}" + (f" ({network_ip})" if network_ip else ""))
                else:
                    summary_lines.append(f"  • {network}")
            summary_lines.append("")

        # Port information - show ALL ports with detailed mapping
        ports = container_info.get("ports", {})
        if ports:
            summary_lines.extend(self._format_port_mappings(ports))

        # Volume information - show ALL volumes without truncation
        volumes = container_info.get("volumes", [])
        mounts = container_info.get("mounts", [])
        all_mounts = volumes + mounts

        if all_mounts:
            summary_lines.append("Volume Mounts:")
            for mount in all_mounts:
                if isinstance(mount, dict):
                    source = mount.get("source", mount.get("Source", ""))
                    target = mount.get("target", mount.get("Destination", ""))
                    mount_type = mount.get("type", mount.get("Type", "bind"))
                    mode = mount.get("mode", mount.get("Mode", "rw"))
                    summary_lines.append(f"  • {source} → {target} ({mount_type}, {mode})")
                else:
                    summary_lines.append(f"  • {mount}")
            summary_lines.append("")

        # Environment variables (if available and not sensitive)
        env_vars = container_info.get("environment", [])
        if env_vars and len(env_vars) <= 20:  # Only show if reasonable number
            summary_lines.append("Environment Variables:")
            for env_var in env_vars[:15]:  # Show up to 15
                # Skip potentially sensitive variables
                if any(sensitive in env_var.upper() for sensitive in ["PASSWORD", "SECRET", "TOKEN", "KEY", "PRIVATE"]):
                    var_name = env_var.split("=")[0] if "=" in env_var else env_var
                    summary_lines.append(f"  • {var_name}=[REDACTED]")
                else:
                    summary_lines.append(f"  • {env_var}")
            if len(env_vars) > 15:
                summary_lines.append(f"  • ... and {len(env_vars) - 15} more variables")
            summary_lines.append("")

        # Compose information
        compose_project = container_info.get("compose_project", "")
        if compose_project:
            summary_lines.append("Docker Compose:")
            summary_lines.append(f"  • Project: {compose_project}")
            compose_file = container_info.get("compose_file", "")
            if compose_file:
                summary_lines.append(f"  • File: {compose_file}")
            compose_service = container_info.get("compose_service", "")
            if compose_service:
                summary_lines.append(f"  • Service: {compose_service}")
            summary_lines.append("")

        # Labels (if available)
        labels = container_info.get("labels", {})
        if labels:
            summary_lines.append("Labels:")
            # Show important Docker/Compose labels first
            important_labels = []
            other_labels = []

            for key, value in labels.items():
                if any(prefix in key for prefix in ["com.docker.compose", "traefik", "org.label-schema"]):
                    important_labels.append((key, value))
                else:
                    other_labels.append((key, value))

            # Show important labels first
            for key, value in important_labels[:10]:
                summary_lines.append(f"  • {key}: {value}")

            # Show other labels (limited)
            for key, value in other_labels[:5]:
                summary_lines.append(f"  • {key}: {value}")

            total_labels = len(important_labels) + len(other_labels)
            if total_labels > 15:
                summary_lines.append(f"  • ... and {total_labels - 15} more labels")
            summary_lines.append("")

        return summary_lines

    def _format_container_details(
        self, container_info: dict[str, Any], container_id: str
    ) -> list[str]:
        """Format detailed container information for display (legacy method - delegates to _format_container_info)."""
        return self._format_container_info(container_info, container_id)

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

    def _format_pull_progress(self, image_name: str, host_id: str, phase: str) -> str:
        """Format image pull progress with visual indicators."""
        if phase == "starting":
            return f"◐ Starting image pull: {image_name}\n  → Host: {host_id}\n  → Status: Initiating pull operation..."
        return f"◐ Pulling {image_name} on {host_id}"

    def _format_pull_success(self, result: dict[str, Any], image_name: str, host_id: str) -> str:
        """Format successful image pull with detailed feedback."""
        message = result.get("message", "Image pulled successfully")

        # Extract useful information from result
        size = result.get("size", "")
        digest = result.get("digest", "")
        layers = result.get("layers", 0)

        formatted_lines = [
            f"✅ Image pull completed: {image_name}",
            f"  → Host: {host_id}",
            f"  → Status: {message}"
        ]

        # Add additional details if available
        if size:
            formatted_lines.append(f"  → Size: {size}")
        if layers and layers > 0:
            formatted_lines.append(f"  → Layers: {layers}")
        if digest:
            formatted_lines.append(f"  → Digest: {digest[:16]}...")

        formatted_lines.append("  ✓ Ready for use")

        return "\n".join(formatted_lines)

    def _format_pull_error(self, result: dict[str, Any], image_name: str, host_id: str) -> str:
        """Format image pull error with helpful context."""
        error_msg = result.get("message", result.get("error", "Unknown error"))

        formatted_lines = [
            f"❌ Image pull failed: {image_name}",
            f"  → Host: {host_id}",
            f"  → Error: {error_msg}"
        ]

        # Add troubleshooting hints based on error type
        if "not found" in error_msg.lower():
            formatted_lines.extend([
                "",
                "Troubleshooting:",
                "  • Verify the image name and tag are correct",
                "  • Check if the image exists in the registry",
                "  • Ensure you have access to the registry"
            ])
        elif "permission" in error_msg.lower() or "unauthorized" in error_msg.lower():
            formatted_lines.extend([
                "",
                "Troubleshooting:",
                "  • Check Docker registry authentication",
                "  • Verify access permissions for the image",
                "  • Try logging in to the registry first"
            ])
        elif "network" in error_msg.lower() or "timeout" in error_msg.lower():
            formatted_lines.extend([
                "",
                "Troubleshooting:",
                "  • Check network connectivity to the registry",
                "  • Verify DNS resolution",
                "  • Try again - network issues may be temporary"
            ])

        return "\n".join(formatted_lines)

    def _format_container_logs(self, logs: list[str], container_id: str, host_id: str, lines_requested: int, truncated: bool) -> str:
        """Format container logs with enhanced structure and visual indicators."""
        if not logs:
            return f"📝 No logs found for {container_id} on {host_id}"

        # Create header with status indicators
        status_icon = "⚠️" if truncated else "📝"
        header_lines = [
            f"{status_icon} Container Logs: {container_id}",
            f"  → Host: {host_id}",
            f"  → Lines: {len(logs)}/{lines_requested}" + (" (truncated)" if truncated else " (complete)"),
            "─" * 60
        ]

        # Process log lines with optional enhancements
        processed_logs = []
        for i, log_line in enumerate(logs):
            # Add line numbers for better reference (optional, only for short logs)
            if len(logs) <= 50:
                processed_logs.append(f"{i+1:3d} | {log_line}")
            else:
                processed_logs.append(log_line)

        # Add footer with additional information
        footer_lines = [
            "─" * 60
        ]

        if truncated:
            footer_lines.append("⚠️  Logs were truncated - use a larger 'lines' parameter to see more")

        footer_lines.append(f"🔍 Use 'docker_container logs {container_id} --lines <N>' for more control")

        all_lines = header_lines + processed_logs + footer_lines
        return "\n".join(all_lines)

    def _format_operation_result(self, result: dict[str, Any], operation: str, context: dict[str, Any]) -> str:
        """Format consistent operation results for start/stop/restart responses with visual indicators."""
        host_id = context.get("host_id", "unknown")
        container_id = context.get("container_id", "unknown")

        if result.get("success"):
            # Success formatting with operation-specific icons and messages
            operation_details = {
                "start": {
                    "icon": "▶️",
                    "action": "started",
                    "next_steps": [
                        "Check logs: docker_container logs",
                        "Monitor status: docker_container info",
                        "View ports: docker_hosts ports"
                    ]
                },
                "stop": {
                    "icon": "⏹️",
                    "action": "stopped",
                    "next_steps": [
                        "Restart if needed: docker_container start",
                        "Remove if done: docker_container remove"
                    ]
                },
                "restart": {
                    "icon": "🔄",
                    "action": "restarted",
                    "next_steps": [
                        "Check logs: docker_container logs",
                        "Verify functionality: docker_container info"
                    ]
                },
                "pause": {
                    "icon": "⏸️",
                    "action": "paused",
                    "next_steps": [
                        "Resume with: docker_container unpause"
                    ]
                },
                "unpause": {
                    "icon": "▶️",
                    "action": "resumed",
                    "next_steps": [
                        "Check status: docker_container info"
                    ]
                },
                "remove": {
                    "icon": "🗑️",
                    "action": "removed",
                    "next_steps": [
                        "Deploy new instance if needed"
                    ]
                }
            }

            details = operation_details.get(operation, {
                "icon": "✅",
                "action": f"{operation}ped",
                "next_steps": []
            })

            formatted_lines = [
                f"{details['icon']} Container {details['action']}: {container_id}",
                f"  → Host: {host_id}",
                f"  → Operation: {operation.title()}",
            ]

            # Add timing information if available
            if result.get("duration"):
                formatted_lines.append(f"  → Duration: {result['duration']}s")

            # Add next steps
            if details["next_steps"]:
                formatted_lines.extend(["", "Next steps:"] + [f"  • {step}" for step in details["next_steps"]])

            return "\n".join(formatted_lines)

        else:
            # Error formatting with troubleshooting hints
            error_msg = result.get("message", result.get("error", "Unknown error"))

            formatted_lines = [
                f"❌ Container {operation} failed: {container_id}",
                f"  → Host: {host_id}",
                f"  → Error: {error_msg}"
            ]

            # Add operation-specific troubleshooting
            if operation == "start":
                formatted_lines.extend([
                    "",
                    "Troubleshooting:",
                    "  • Check if container is already running",
                    "  • Verify port conflicts: docker_hosts ports",
                    "  • Check container logs for errors",
                    "  • Ensure sufficient resources (CPU, memory)"
                ])
            elif operation == "stop":
                formatted_lines.extend([
                    "",
                    "Troubleshooting:",
                    "  • Try with force flag if container is unresponsive",
                    "  • Check if container is already stopped",
                    "  • Verify container exists: docker_container info"
                ])
            elif operation == "restart":
                formatted_lines.extend([
                    "",
                    "Troubleshooting:",
                    "  • Try stop then start separately",
                    "  • Check container health before restart",
                    "  • Verify no resource conflicts"
                ])

            return "\n".join(formatted_lines)

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

            # Use new _format_operation_result for consistent formatting
            context = {"host_id": host_id, "container_id": container_id}
            formatted_text = self._format_operation_result(enhanced_result, action, context)
            enhanced_result["formatted_output"] = formatted_text

            return ToolResult(
                content=[TextContent(type="text", text=formatted_text)],
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
            formatted_text = f"❌ Failed to {action} container: {str(e)}"
            return ToolResult(
                content=[TextContent(type="text", text=formatted_text)],
                structured_content={
                    "success": False,
                    "error": str(e),
                    HOST_ID: host_id,
                    CONTAINER_ID: container_id,
                    "action": action,
                    "formatted_output": formatted_text,
                },
            )

    async def pull_image(self, host_id: str, image_name: str) -> ToolResult:
        """Pull a Docker image on a remote host with enhanced progress indicators."""
        try:
            is_valid, error_msg = validate_host(self.config, host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Enhanced formatting for pull operation with progress indicators
            formatted_text = self._format_pull_progress(image_name, host_id, "starting")

            # Use container tools to pull image
            result = await self.container_tools.pull_image(host_id, image_name)

            if result["success"]:
                formatted_text = self._format_pull_success(result, image_name, host_id)
                result = dict(result)
                result["formatted_output"] = formatted_text
                return ToolResult(
                    content=[TextContent(type="text", text=formatted_text)],
                    structured_content=result,
                )
            else:
                formatted_text = self._format_pull_error(result, image_name, host_id)
                result = dict(result)
                result["formatted_output"] = formatted_text
                return ToolResult(
                    content=[TextContent(type="text", text=formatted_text)],
                    structured_content=result,
                )

        except Exception as e:
            self.logger.error(
                "Failed to pull image",
                host_id=host_id,
                image_name=image_name,
                error=str(e),
            )
            formatted_text = f"❌ Image pull failed: {image_name}\n  Host: {host_id}\n  Error: {str(e)}"
            return ToolResult(
                content=[TextContent(type="text", text=formatted_text)],
                structured_content={
                    "success": False,
                    "error": str(e),
                    HOST_ID: host_id,
                    "image_name": image_name,
                    "formatted_output": formatted_text,
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

            # Extract data from the response structure
            data = result.get("data", {})

            summary_lines = self._format_port_usage_summary(result, host_id)
            formatted_text = "\n".join(summary_lines)

            return ToolResult(
                content=[TextContent(type="text", text=formatted_text)],
                structured_content={
                    "success": True,
                    HOST_ID: host_id,
                    "total_ports": data.get("total_ports", 0),
                    "total_containers": data.get("total_containers", 0),
                    "port_mappings": data.get("port_mappings", []),
                    "conflicts": data.get("conflicts", []),
                    "summary": data.get("summary", {}),
                    "cached": result.get("cached", False),
                    "timestamp": result.get("timestamp"),
                    "formatted_output": formatted_text,
                },
            )

        except Exception as e:
            self.logger.error("Failed to list host ports", host_id=host_id, error=str(e))
            formatted_text = f"❌ Failed to list host ports: {str(e)}"
            return ToolResult(
                content=[TextContent(type="text", text=formatted_text)],
                structured_content={
                    "success": False,
                    "error": str(e),
                    HOST_ID: host_id,
                    "formatted_output": formatted_text,
                },
            )

    def _format_port_usage_summary(self, result: dict[str, Any], host_id: str) -> list[str]:
        """Format comprehensive port usage summary."""
        data = result.get("data", {})
        port_mappings = data.get("port_mappings", [])
        conflicts = data.get("conflicts", [])
        summary = data.get("summary", {})

        summary_lines = [
            f"Port Usage on {host_id}",
            f"Found {data.get('total_ports', 0)} exposed ports across {data.get('total_containers', 0)} containers",
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
                return {
                    "success": False,
                    "error": error_msg,
                    "formatted_output": f"❌ Port check failed: {error_msg}",
                }

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

            response: dict[str, Any] = {
                "success": True,
                HOST_ID: host_id,
                "port": port,
                "available": is_available,
                "conflicts": conflicts,
                "message": f"Port {port} is {'available' if is_available else 'in use'}",
            }

            conflicts_preview = []
            if conflicts:
                for conflict in conflicts[:5]:
                    name = conflict.get("container_name", "unknown")
                    protocol = conflict.get("protocol", "tcp").upper()
                    conflicts_preview.append(f"  • {name} ({protocol})")
                remaining = len(conflicts) - len(conflicts_preview)
                if remaining > 0:
                    conflicts_preview.append(f"  • +{remaining} more")

            if is_available:
                response["formatted_output"] = (
                    f"Port {port} is available on {host_id}"
                )
            else:
                formatted_lines = [
                    f"Port {port} is in use on {host_id}",
                ]
                if conflicts_preview:
                    formatted_lines.append("Conflicts:")
                    formatted_lines.extend(conflicts_preview)
                response["formatted_output"] = "\n".join(formatted_lines)

            return response

        except Exception as e:
            self.logger.error(
                "Failed to check port availability", host_id=host_id, port=port, error=str(e)
            )
            return {
                "success": False,
                "error": f"Port check failed: {str(e)}",
                HOST_ID: host_id,
                "port": port,
                "formatted_output": f"❌ Port check failed: {str(e)}",
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
            image_name = params.get("image_name", "")
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
            elif action == ContainerAction.PULL or (isinstance(action, str) and action == "pull"):
                return await self._handle_pull_action(host_id, image_name or container_id)
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

            logs: list[str] = []
            truncated = False

            if isinstance(logs_result, dict):
                # Preferred shape: success response with payload under "data"
                if isinstance(logs_result.get("data"), dict):
                    data = logs_result["data"]
                    logs = data.get("logs", []) or []
                    truncated = data.get("truncated", False)
                # Legacy shape: logs returned at the top level
                elif "logs" in logs_result:
                    logs = logs_result.get("logs", []) or []
                    truncated = logs_result.get("truncated", False)

            # Ensure we always return a list even if upstream gave us something unexpected
            if not isinstance(logs, list):
                logs = []

            # Enhanced logs formatting with better structure and visual indicators
            formatted_text = self._format_container_logs(logs, container_id, host_id, lines, truncated)

            return {
                "success": True,
                "host_id": host_id,
                "container_id": container_id,
                "logs": logs,
                "lines_requested": lines,
                "lines_returned": len(logs),
                "truncated": truncated,
                "follow": follow,
                "formatted_output": formatted_text,
            }

        except Exception as e:
            return self._build_error_response(
                host_id=host_id,
                container_id=container_id,
                action="logs",
                error=e,
                message="Failed to get container logs",
            )

    async def _handle_pull_action(self, host_id: str, image_name: str) -> dict[str, Any]:
        """Handle image pull action."""
        if not host_id:
            return self._build_error_response(
                host_id="",
                container_id=None,
                action="pull",
                error=ValueError("host_id missing"),
                message="host_id is required for pull action",
            )
        if not image_name:
            return self._build_error_response(
                host_id=host_id,
                container_id=None,
                action="pull",
                error=ValueError("image name missing"),
                message="image_name is required for pull action",
            )

        result = await self.pull_image(host_id, image_name)
        return self._extract_structured_content(result)

    def _handle_unknown_action(self, action) -> dict[str, Any]:
        """Handle unknown action."""
        formatted_text = f"❌ Unknown action: {action}"
        return {
            "success": False,
            "error": f"Unknown action: {action}",
            "valid_actions": [
                "list",
                "info",
                "start",
                "stop",
                "restart",
                "remove",
                "logs",
                "pull",
            ],
            "formatted_output": formatted_text,
        }

    def _extract_structured_content(self, result) -> dict[str, Any]:
        """Extract structured content from ToolResult."""
        if hasattr(result, "structured_content") and result.structured_content is not None:
            structured = result.structured_content
            if not isinstance(structured, dict):
                structured = dict(structured)
            else:
                structured = dict(structured)

            formatted_text = ""
            if hasattr(result, "content") and result.content:
                first_content = result.content[0]
                formatted_text = getattr(first_content, "text", "") or ""
            if formatted_text and "formatted_output" not in structured:
                structured["formatted_output"] = formatted_text

            if "formatted_output" in structured:
                formatted_value = structured["formatted_output"]
                ordered = {"formatted_output": formatted_value}
                for key, value in structured.items():
                    if key == "formatted_output":
                        continue
                    ordered[key] = value
                return ordered

            return structured
        return {"success": False, "error": "Invalid result format"}
