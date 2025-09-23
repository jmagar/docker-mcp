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

    async def deploy_stack_with_partial_failure_handling(
        self,
        host_id: str,
        stack_name: str,
        compose_content: str,
        environment: dict[str, str] | None = None,
        pull_images: bool = True,
        recreate: bool = False,
    ) -> ToolResult:
        """Deploy a Docker Compose stack with comprehensive partial failure handling."""
        try:
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Track service-level results
            service_results = {
                "successful_services": [],
                "failed_services": [],
                "partial_success": False,
                "recovery_options": []
            }

            # First, attempt normal deployment
            result = await self.stack_tools.deploy_stack(
                host_id, stack_name, compose_content, environment, pull_images, recreate
            )

            if result["success"]:
                # Deployment succeeded, but verify individual services
                await self._verify_service_status(host_id, stack_name, service_results)
                
                if service_results["failed_services"]:
                    service_results["partial_success"] = True
                    service_results["recovery_options"] = [
                        "retry_failed_services",
                        "restart_failed_services", 
                        "rollback_stack"
                    ]
                
                return ToolResult(
                    content=[TextContent(
                        type="text", 
                        text=self._format_deployment_result(stack_name, result, service_results)
                    )],
                    structured_content={**result, "service_details": service_results},
                )
            else:
                # Deployment failed, check for partial services that may have started
                await self._analyze_partial_deployment(host_id, stack_name, service_results)
                
                if service_results["successful_services"]:
                    service_results["partial_success"] = True
                    service_results["recovery_options"] = [
                        "retry_failed_services",
                        "rollback_successful_services",
                        "complete_manual_deployment"
                    ]
                
                return ToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"Deployment failed with partial success. {self._format_deployment_result(stack_name, result, service_results)}"
                    )],
                    structured_content={**result, "service_details": service_results},
                )

        except Exception as e:
            self.logger.error(
                "Failed to deploy stack with partial failure handling", 
                host_id=host_id, 
                stack_name=stack_name, 
                error=str(e)
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
                # Briefly wait for the project to become visible in list_stacks
                try:
                    import asyncio as _asyncio

                    await _asyncio.sleep(0.5)  # Initial delay for deployment to settle
                    for _ in range(5):
                        list_result = await self.stack_tools.list_stacks(host_id)
                        if any(
                            isinstance(s, dict) and s.get("name", "").lower() == stack_name.lower()
                            for s in list_result.get("stacks", [])
                        ):
                            break
                        await _asyncio.sleep(1)
                except Exception as e:
                    self.logger.debug(
                        "Stack deployment verification failed",
                        host_id=host_id,
                        stack_name=stack_name,
                        error=str(e),
                        error_type=type(e).__name__,
                    )
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
        """Format stacks list for display - compact table format."""
        stacks = result["stacks"]

        # Count stacks by status
        status_counts = {}
        for stack in stacks:
            status = stack.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1

        status_summary = ", ".join(f"{status}: {count}" for status, count in status_counts.items())

        summary_lines = [
            f"Docker Compose Stacks on {host_id} ({len(stacks)} total)",
            f"Status breakdown: {status_summary}",
            "",
            f"{'':1} {'Stack':<25} {'Status':<10} {'Services':<15}",
            f"{'':1} {'-' * 25:<25} {'-' * 10:<10} {'-' * 15:<15}",
        ]

        for stack in stacks:
            status_indicator = {"running": "●", "partial": "◐", "stopped": "○"}.get(
                stack.get("status", "unknown"), "?"
            )
            services = stack.get("services", [])
            services_display = f"[{len(services)}] {','.join(services[:2])}" if services else "[0]"
            if len(services) > 2:
                services_display += "..."

            stack_name = stack["name"][:24]  # Truncate long names
            status = stack.get("status", "unknown")[:9]  # Truncate status

            summary_lines.append(
                f"{status_indicator} {stack_name:<25} {status:<10} {services_display[:15]:<15}"
            )

        return summary_lines

    async def _verify_service_status(self, host_id: str, stack_name: str, service_results: dict) -> None:
        """Verify the status of individual services after deployment."""
        try:
            # Get stack services status
            ps_result = await self.stack_tools.manage_stack(host_id, stack_name, "ps")
            
            if ps_result.get("success") and ps_result.get("data", {}).get("services"):
                services = ps_result["data"]["services"]
                
                for service in services:
                    service_name = service.get("Name", "Unknown")
                    service_status = service.get("Status", "").lower()
                    
                    service_info = {
                        "name": service_name,
                        "status": service_status,
                        "container_id": service.get("ID", ""),
                        "image": service.get("Image", "")
                    }
                    
                    if "running" in service_status or "up" in service_status:
                        service_results["successful_services"].append(service_info)
                    else:
                        service_results["failed_services"].append(service_info)
                        
        except Exception as e:
            self.logger.warning(
                "Failed to verify service status", 
                host_id=host_id, 
                stack_name=stack_name, 
                error=str(e)
            )
            # Add a generic failure indication
            service_results["failed_services"].append({
                "name": "verification_failed",
                "status": "unknown",
                "error": str(e)
            })

    async def _analyze_partial_deployment(self, host_id: str, stack_name: str, service_results: dict) -> None:
        """Analyze what services may have started despite deployment failure."""
        try:
            # Check if any containers from this stack are running
            list_result = await self.stack_tools.list_stacks(host_id)
            
            if list_result.get("success") and list_result.get("stacks"):
                for stack in list_result["stacks"]:
                    if stack.get("name") == stack_name:
                        services = stack.get("services", [])
                        stack_status = stack.get("status", "unknown")
                        
                        # If stack has partial status, some services might be running
                        if stack_status == "partial" or services:
                            for service_name in services:
                                service_results["successful_services"].append({
                                    "name": service_name,
                                    "status": "partially_running",
                                    "container_id": "unknown"
                                })
                        break
                        
        except Exception as e:
            self.logger.warning(
                "Failed to analyze partial deployment", 
                host_id=host_id, 
                stack_name=stack_name, 
                error=str(e)
            )

    def _format_deployment_result(self, stack_name: str, result: dict, service_results: dict) -> str:
        """Format deployment result with service details."""
        lines = []
        
        if result.get("success"):
            lines.append(f"✓ Stack '{stack_name}' deployed successfully")
        else:
            lines.append(f"✗ Stack '{stack_name}' deployment failed: {result.get('error', 'Unknown error')}")
        
        # Add service details
        if service_results.get("successful_services"):
            lines.append(f"\nSuccessful services ({len(service_results['successful_services'])}):")
            for service in service_results["successful_services"]:
                lines.append(f"  ✓ {service['name']}: {service.get('status', 'running')}")
        
        if service_results.get("failed_services"):
            lines.append(f"\nFailed services ({len(service_results['failed_services'])}):")
            for service in service_results["failed_services"]:
                lines.append(f"  ✗ {service['name']}: {service.get('status', 'failed')}")
        
        # Add recovery options
        if service_results.get("recovery_options"):
            lines.append(f"\nRecovery options available:")
            for option in service_results["recovery_options"]:
                lines.append(f"  • {option.replace('_', ' ').title()}")
        
        if service_results.get("partial_success"):
            lines.append(f"\n⚠️  Partial deployment detected - some services may need attention")
        
        return "\n".join(lines)

    async def retry_failed_services(self, host_id: str, stack_name: str, failed_services: list[str]) -> ToolResult:
        """Retry deployment for specific failed services."""
        try:
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            retry_results = {
                "retried_services": [],
                "successful_retries": [],
                "failed_retries": []
            }

            for service_name in failed_services:
                try:
                    # Try to restart the specific service
                    restart_result = await self.stack_tools.manage_stack(
                        host_id, stack_name, "restart", {"services": [service_name]}
                    )
                    
                    retry_results["retried_services"].append(service_name)
                    
                    if restart_result.get("success"):
                        retry_results["successful_retries"].append(service_name)
                    else:
                        retry_results["failed_retries"].append({
                            "service": service_name,
                            "error": restart_result.get("error", "Unknown error")
                        })
                        
                except Exception as e:
                    retry_results["failed_retries"].append({
                        "service": service_name,
                        "error": str(e)
                    })

            # Format result message
            message_lines = [f"Service retry operation completed for stack '{stack_name}'"]
            
            if retry_results["successful_retries"]:
                message_lines.append(f"✓ Successfully restarted: {', '.join(retry_results['successful_retries'])}")
            
            if retry_results["failed_retries"]:
                message_lines.append(f"✗ Failed to restart:")
                for failure in retry_results["failed_retries"]:
                    message_lines.append(f"  • {failure['service']}: {failure['error']}")

            overall_success = len(retry_results["successful_retries"]) > 0 and len(retry_results["failed_retries"]) == 0

            return ToolResult(
                content=[TextContent(type="text", text="\n".join(message_lines))],
                structured_content={
                    "success": overall_success,
                    "retry_results": retry_results,
                    "host_id": host_id,
                    "stack_name": stack_name
                }
            )

        except Exception as e:
            self.logger.error(
                "Failed to retry services", 
                host_id=host_id, 
                stack_name=stack_name, 
                services=failed_services,
                error=str(e)
            )
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to retry services: {str(e)}")],
                structured_content={
                    "success": False,
                    "error": str(e),
                    "host_id": host_id,
                    "stack_name": stack_name,
                },
            )

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
