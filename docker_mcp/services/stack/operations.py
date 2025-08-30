"""
Stack Operations Module

Basic CRUD operations for Docker Compose stack management.
Handles deployment, lifecycle management, listing, and compose file retrieval.
"""

from typing import Any

import structlog
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from ...core.config_loader import DockerMCPConfig
from ...core.docker_context import DockerContextManager
from ...tools.stacks import StackTools


class StackOperations:
    """Core stack operations: deploy, manage, list, and compose file retrieval."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager
        self.stack_tools = StackTools(config, context_manager)
        self.logger = structlog.get_logger()

    def _validate_host(self, host_id: str) -> tuple[bool, str]:
        """Validate host exists in configuration."""
        if host_id not in self.config.hosts:
            return False, f"Host '{host_id}' not found"
        return True, ""

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
        try:
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use stack tools to deploy
            result = await self.stack_tools.deploy_stack(
                host_id, stack_name, compose_content, environment, pull_images, recreate
            )

            if result["success"]:
                return ToolResult(
                    content=[
                        TextContent(
                            type="text", text=f"Success: Stack '{stack_name}' deployed to {host_id}"
                        )
                    ],
                    structured_content=result,
                )
            else:
                return ToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"Error: Failed to deploy stack '{stack_name}': {result.get('error', 'Unknown error')}",
                        )
                    ],
                    structured_content=result,
                )

        except Exception as e:
            self.logger.error(
                "Failed to deploy stack", host_id=host_id, stack_name=stack_name, error=str(e)
            )
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to deploy stack: {str(e)}")],
                structured_content={
                    "success": False,
                    "error": str(e),
                    "host_id": host_id,
                    "stack_name": stack_name,
                },
            )

    async def manage_stack(
        self, host_id: str, stack_name: str, action: str, options: dict[str, Any] | None = None
    ) -> ToolResult:
        """Unified stack lifecycle management."""
        try:
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use stack tools to manage stack
            result = await self.stack_tools.manage_stack(host_id, stack_name, action, options)

            if result["success"]:
                message_lines = self._format_stack_action_result(result, stack_name, action)

                return ToolResult(
                    content=[TextContent(type="text", text="\n".join(message_lines))],
                    structured_content=result,
                )
            else:
                return ToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"Error: Failed to {action} stack '{stack_name}': {result.get('error', 'Unknown error')}",
                        )
                    ],
                    structured_content=result,
                )

        except Exception as e:
            self.logger.error(
                "Failed to manage stack",
                host_id=host_id,
                stack_name=stack_name,
                action=action,
                error=str(e),
            )
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to {action} stack: {str(e)}")],
                structured_content={
                    "success": False,
                    "error": str(e),
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "action": action,
                },
            )

    def _format_stack_action_result(
        self, result: dict[str, Any], stack_name: str, action: str
    ) -> list[str]:
        """Format stack action result for display."""
        message_lines = [f"Success: Stack '{stack_name}' {action} completed"]

        # Add specific output for certain actions
        if action == "ps" and result.get("data", {}).get("services"):
            services = result["data"]["services"]
            message_lines.append("\nServices:")
            for service in services:
                name = service.get("Name", "Unknown")
                status = service.get("Status", "Unknown")
                message_lines.append(f"  {name}: {status}")

        return message_lines

    async def list_stacks(self, host_id: str) -> ToolResult:
        """List Docker Compose stacks on a host."""
        try:
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use stack tools to list stacks
            result = await self.stack_tools.list_stacks(host_id)

            if result["success"]:
                summary_lines = self._format_stacks_list(result, host_id)

                return ToolResult(
                    content=[TextContent(type="text", text="\n".join(summary_lines))],
                    structured_content=result,
                )
            else:
                return ToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"Error: Failed to list stacks: {result.get('error', 'Unknown error')}",
                        )
                    ],
                    structured_content=result,
                )

        except Exception as e:
            self.logger.error("Failed to list stacks", host_id=host_id, error=str(e))
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to list stacks: {str(e)}")],
                structured_content={"success": False, "error": str(e), "host_id": host_id},
            )

    def _format_stacks_list(self, result: dict[str, Any], host_id: str) -> list[str]:
        """Format stacks list for display."""
        stacks = result["stacks"]
        summary_lines = [
            f"Docker Compose Stacks on {host_id}",
            f"Found {len(stacks)} stacks",
            "",
        ]

        for stack in stacks:
            status_indicator = "●" if "running" in stack.get("status", "").lower() else "○"
            services = stack.get("services", [])
            services_info = f" ({len(services)} services)" if services else ""

            summary_lines.append(
                f"{status_indicator} {stack['name']}{services_info}\n"
                f"    Status: {stack.get('status', 'Unknown')}\n"
                f"    Created: {stack.get('created', 'Unknown')}"
            )

        return summary_lines

    async def get_stack_compose_file(self, host_id: str, stack_name: str) -> ToolResult:
        """Get the docker-compose.yml content for a specific stack."""
        try:
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use stack tools to get the compose file content
            result = await self.stack_tools.get_stack_compose_content(host_id, stack_name)

            if result["success"]:
                compose_content = result.get("compose_content", "")
                return ToolResult(
                    content=[TextContent(type="text", text=compose_content)],
                    structured_content={
                        "success": True,
                        "host_id": host_id,
                        "stack_name": stack_name,
                        "compose_content": compose_content,
                    },
                )
            else:
                return ToolResult(
                    content=[TextContent(type="text", text=f"❌ {result['error']}")],
                    structured_content=result,
                )

        except Exception as e:
            self.logger.error(
                "Failed to get stack compose file",
                host_id=host_id,
                stack_name=stack_name,
                error=str(e),
            )
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to get compose file: {str(e)}")],
                structured_content={
                    "success": False,
                    "error": str(e),
                    "host_id": host_id,
                    "stack_name": stack_name,
                },
            )
