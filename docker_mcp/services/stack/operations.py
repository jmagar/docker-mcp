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

                formatted_text = self._format_deployment_result(stack_name, result, service_results)
                structured = {**result, "service_details": service_results, "formatted_output": formatted_text}
                return ToolResult(
                    content=[TextContent(type="text", text=formatted_text)],
                    structured_content=structured,
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

                formatted_text = (
                    "Deployment failed with partial success. "
                    f"{self._format_deployment_result(stack_name, result, service_results)}"
                )
                structured = {**result, "service_details": service_results, "formatted_output": formatted_text}
                return ToolResult(
                    content=[TextContent(type="text", text=formatted_text)],
                    structured_content=structured,
                )

        except Exception as e:
            self.logger.error(
                "Failed to deploy stack with partial failure handling",
                host_id=host_id,
                stack_name=stack_name,
                error=str(e)
            )
            formatted_text = f"❌ Failed to deploy stack: {str(e)}"
            return ToolResult(
                content=[TextContent(type="text", text=formatted_text)],
                structured_content={
                    "success": False,
                    "error": str(e),
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "formatted_output": formatted_text,
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
                # Use enhanced deployment result formatting
                formatted_lines = self._format_deploy_result(result, stack_name, host_id)
                formatted_text = "\n".join(formatted_lines)
                structured = dict(result)
                structured["formatted_output"] = formatted_text
                return ToolResult(
                    content=[TextContent(type="text", text=formatted_text)],
                    structured_content=structured,
                )
            else:
                formatted_text = (
                    "❌ Failed to deploy stack '",
                )
                formatted_text = (
                    f"❌ Failed to deploy stack '{stack_name}': {result.get('error', 'Unknown error')}"
                )
                structured = dict(result)
                structured["formatted_output"] = formatted_text
                return ToolResult(
                    content=[TextContent(type="text", text=formatted_text)],
                    structured_content=structured,
                )

        except Exception as e:
            self.logger.error(
                "Failed to deploy stack", host_id=host_id, stack_name=stack_name, error=str(e)
            )
            formatted_text = f"❌ Failed to deploy stack: {str(e)}"
            return ToolResult(
                content=[TextContent(type="text", text=formatted_text)],
                structured_content={
                    "success": False,
                    "error": str(e),
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "formatted_output": formatted_text,
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
                formatted_text = "\n".join(message_lines)
                structured = dict(result)
                structured["formatted_output"] = formatted_text

                return ToolResult(
                    content=[TextContent(type="text", text=formatted_text)],
                    structured_content=structured,
                )
            else:
                formatted_text = (
                    f"❌ Failed to {action} stack '{stack_name}': {result.get('error', 'Unknown error')}"
                )
                structured = dict(result)
                structured["formatted_output"] = formatted_text
                return ToolResult(
                    content=[TextContent(type="text", text=formatted_text)],
                    structured_content=structured,
                )

        except Exception as e:
            self.logger.error(
                "Failed to manage stack",
                host_id=host_id,
                stack_name=stack_name,
                action=action,
                error=str(e),
            )
            formatted_text = f"❌ Failed to {action} stack: {str(e)}"
            return ToolResult(
                content=[TextContent(type="text", text=formatted_text)],
                structured_content={
                    "success": False,
                    "error": str(e),
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "action": action,
                    "formatted_output": formatted_text,
                },
            )

    def _format_stack_action_result(
        self, result: dict[str, Any], stack_name: str, action: str
    ) -> list[str]:
        """Format stack action result for display with enhanced visual formatting."""

        # Special handling for ps action
        if action == "ps":
            return self._format_ps_result(result, stack_name)

        # Header with action-specific emoji
        action_emoji = {
            "up": "🚀",
            "down": "🛑",
            "restart": "🔄",
            "build": "🔨",
            "pull": "📦",
            "logs": "📋"
        }

        emoji = action_emoji.get(action, "⚙️")
        message_lines = []
        message_lines.append("─" * 50)
        message_lines.append(f"{emoji} Stack {action.title()}: {stack_name}")
        message_lines.append("─" * 50)

        if result.get("success"):
            message_lines.append(f"✅ Status: {action.upper()} completed successfully")
        else:
            message_lines.append(f"❌ Status: {action.upper()} failed")
            if error := result.get("error"):
                message_lines.append(f"   Error: {error}")

        # Add output if available
        if output := result.get("output"):
            message_lines.append("")
            message_lines.append("📄 Command Output:")
            message_lines.append("┌" + "─" * 48 + "┐")
            for line in output.split("\n")[-10:]:  # Show last 10 lines
                if line.strip():
                    message_lines.append(f"│ {line[:46]:<46} │")
            message_lines.append("└" + "─" * 48 + "┘")

        message_lines.append("")

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
                formatted_text = "\n".join(summary_lines)
                structured = dict(result)
                structured["formatted_output"] = formatted_text

                return ToolResult(
                    content=[TextContent(type="text", text=formatted_text)],
                    structured_content=structured,
                )
            else:
                formatted_text = (
                    f"❌ Failed to list stacks: {result.get('error', 'Unknown error')}"
                )
                structured = dict(result)
                structured["formatted_output"] = formatted_text
                return ToolResult(
                    content=[TextContent(type="text", text=formatted_text)],
                    structured_content=structured,
                )

        except Exception as e:
            self.logger.error("Failed to list stacks", host_id=host_id, error=str(e))
            formatted_text = f"❌ Failed to list stacks: {str(e)}"
            return ToolResult(
                content=[TextContent(type="text", text=formatted_text)],
                structured_content={
                    "success": False,
                    "error": str(e),
                    "host_id": host_id,
                    "formatted_output": formatted_text,
                },
            )

    def _format_stacks_list(self, result: dict[str, Any], host_id: str) -> list[str]:
        """Format stacks list for display - enhanced visual hierarchy with NO truncation."""
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
        ]

        summary_lines.extend([
            "  Stack                     Status     Services",
            "  ------------------------- ---------- --------------------",
        ])

        for stack in stacks:
            status_indicator = {"running": "●", "partial": "◐", "stopped": "○"}.get(
                stack.get("status", "unknown"), "?"
            )
            services = stack.get("services", [])

            if services:
                services_display = f"[{len(services)}] {', '.join(services[:2])}"
                if len(services) > 2:
                    services_display += "..."
            else:
                services_display = "[0]"

            stack_name = stack.get("name", "unknown")[:25]
            status = stack.get("status", "unknown")[:10]

            summary_lines.append(
                f"{status_indicator} {stack_name:<25} {status:<10} {services_display}"
            )

        return summary_lines

    def _format_deploy_result(self, result: dict[str, Any], stack_name: str, host_id: str) -> list[str]:
        """Format deployment result with service-level progress visualization."""
        lines = []

        # Header with visual separator
        lines.append("═" * 60)
        lines.append(f"🚀 Stack Deployment: {stack_name} → {host_id}")
        lines.append("═" * 60)

        if result.get("success"):
            lines.append("✅ Deployment Status: SUCCESS")
        else:
            lines.append("❌ Deployment Status: FAILED")
            if error := result.get("error"):
                lines.append(f"   Error: {error}")

        lines.append("")

        # Service deployment status
        services_data = result.get("data", {}).get("services", [])
        if services_data:
            lines.append("📋 Service Deployment Status:")
            lines.append("─" * 40)

            for service in services_data:
                name = service.get("Name", "Unknown")
                status = service.get("Status", "Unknown").lower()

                # Enhanced health indicators
                if "running" in status or "up" in status:
                    if "healthy" in status:
                        indicator = "✅"
                        status_text = "Healthy & Running"
                    else:
                        indicator = "🟢"
                        status_text = "Running"
                elif "starting" in status or "restarting" in status:
                    indicator = "🔄"
                    status_text = "Starting"
                elif "unhealthy" in status:
                    indicator = "⚠️ "
                    status_text = "Unhealthy"
                elif "exited" in status or "stopped" in status:
                    indicator = "🔴"
                    status_text = "Stopped"
                else:
                    indicator = "❓"
                    status_text = status.title()

                lines.append(f"  {indicator} {name:<25} │ {status_text}")

            lines.append("")

        # Additional deployment info
        if pull_info := result.get("pull_images"):
            lines.append(f"📦 Images: {'Pulled' if pull_info else 'Used cached'}")

        if recreate_info := result.get("recreate"):
            lines.append(f"🔄 Recreation: {'Forced' if recreate_info else 'Incremental'}")

        # Show deployment path
        if compose_path := result.get("compose_path"):
            lines.append(f"📁 Deployed to: {compose_path}")

        lines.append("")
        lines.append("═" * 60)

        return lines

    def _format_ps_result(self, result: dict[str, Any], stack_name: str) -> list[str]:
        """Format ps action result with enhanced health status indicators."""
        summary_lines: list[str] = [f"Stack Services: {stack_name}"]

        services = result.get("data", {}).get("services", [])
        if not services:
            summary_lines.append("No services reported (stack may be stopped)")
            return summary_lines

        summary_lines.append("")
        summary_lines.append(f"{'State':<7} {'Service':<25} {'Health':<10} Ports")
        summary_lines.append(
            f"{'-' * 7:<7} {'-' * 25:<25} {'-' * 10:<10} {'-' * 30}"
        )

        status_counts = {"healthy": 0, "unhealthy": 0, "starting": 0, "stopped": 0}

        for service in services:
            name = service.get("Name", "Unknown")
            status = (service.get("Status") or "").lower()
            ports = (service.get("Ports") or "-").replace("->", "→")

            if "running" in status or "up" in status:
                if "healthy" in status:
                    state_icon = "●"
                    health_label = "healthy"
                    status_counts["healthy"] += 1
                elif "unhealthy" in status:
                    state_icon = "●"
                    health_label = "unhealthy"
                    status_counts["unhealthy"] += 1
                else:
                    state_icon = "●"
                    health_label = "running"
                    status_counts["healthy"] += 1
            elif "starting" in status or "restarting" in status:
                state_icon = "◐"
                health_label = "starting"
                status_counts["starting"] += 1
            elif "exited" in status or "stopped" in status:
                state_icon = "○"
                health_label = "stopped"
                status_counts["stopped"] += 1
            else:
                state_icon = "?"
                health_label = "unknown"
                status_counts["stopped"] += 1

            summary_lines.append(
                f"{state_icon:<7} {name[:25]:<25} {health_label:<10} {ports}"
            )

        totals: list[str] = []
        if status_counts["healthy"]:
            totals.append(f"healthy {status_counts['healthy']}")
        if status_counts["unhealthy"]:
            totals.append(f"unhealthy {status_counts['unhealthy']}")
        if status_counts["starting"]:
            totals.append(f"starting {status_counts['starting']}")
        if status_counts["stopped"]:
            totals.append(f"stopped {status_counts['stopped']}")

        summary_lines.append("")
        total_services = len(services)
        if totals:
            summary_lines.append(f"Summary: {total_services} services ({', '.join(totals)})")
        else:
            summary_lines.append(f"Summary: {total_services} services")

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
        """Format deployment result with enhanced service details and visual hierarchy."""
        lines = []

        # Enhanced header with visual separation
        lines.append("═" * 60)
        if result.get("success"):
            lines.append(f"✅ Stack '{stack_name}' deployed successfully")
        else:
            lines.append(f"❌ Stack '{stack_name}' deployment failed: {result.get('error', 'Unknown error')}")
        lines.append("═" * 60)

        # Service status details with enhanced formatting
        if service_results.get("successful_services"):
            lines.append(f"\n🟢 Successful Services ({len(service_results['successful_services'])}):")
            lines.append("─" * 40)
            for service in service_results["successful_services"]:
                status = service.get('status', 'running')
                image = service.get('image', '')
                lines.append(f"  ✅ {service['name']:<25} │ {status}")
                if image:
                    lines.append(f"     └─ Image: {image}")

        if service_results.get("failed_services"):
            lines.append(f"\n🔴 Failed Services ({len(service_results['failed_services'])}):")
            lines.append("─" * 40)
            for service in service_results["failed_services"]:
                status = service.get('status', 'failed')
                error = service.get('error', '')
                lines.append(f"  ❌ {service['name']:<25} │ {status}")
                if error and error != 'failed':
                    lines.append(f"     └─ Error: {error}")

        # Recovery options with enhanced formatting
        if service_results.get("recovery_options"):
            lines.append("\n🔧 Recovery Options Available:")
            lines.append("─" * 30)
            for option in service_results["recovery_options"]:
                option_text = option.replace('_', ' ').title()
                lines.append(f"  🔄 {option_text}")

        if service_results.get("partial_success"):
            lines.append("\n⚠️  Partial deployment detected - manual intervention may be required")
            lines.append("    Consider using recovery options above to resolve issues")

        lines.append("═" * 60)

        return "\n".join(lines)

    def _format_migrate_result(self, result: dict[str, Any], stack_name: str, source_host: str, target_host: str) -> list[str]:
        """Format migration result with clear step-by-step progress visualization."""
        lines = []

        # Header with migration flow
        lines.append("╔" + "═" * 58 + "╗")
        lines.append(f"║ 🚚 Stack Migration: {stack_name:<35} ║")
        lines.append(f"║ {source_host} ➡️  {target_host:<42} ║")
        lines.append("╚" + "═" * 58 + "╝")
        lines.append("")

        # Overall migration status
        if result.get("overall_success") or result.get("success"):
            lines.append("✅ Migration Status: COMPLETED SUCCESSFULLY")
        else:
            lines.append("❌ Migration Status: FAILED")
            if error := result.get("error"):
                lines.append(f"   Primary Error: {error}")

        lines.append("")

        # Migration steps with detailed progress
        steps = result.get("migration_steps", [])
        if steps:
            lines.append("📋 Migration Progress:")
            lines.append("┌" + "─" * 56 + "┐")

            for i, step in enumerate(steps, 1):
                step_name = step.get("name", f"Step {i}")
                step_status = step.get("status", "unknown")
                step_duration = step.get("duration_seconds", 0)

                if step_status == "completed":
                    status_icon = "✅"
                    status_text = f"Completed ({step_duration:.1f}s)"
                elif step_status == "failed":
                    status_icon = "❌"
                    status_text = f"Failed ({step_duration:.1f}s)"
                    if step_error := step.get("error"):
                        status_text += f" - {step_error}"
                elif step_status == "in_progress":
                    status_icon = "🔄"
                    status_text = "In Progress..."
                elif step_status == "skipped":
                    status_icon = "⏭️"
                    status_text = "Skipped"
                else:
                    status_icon = "⏸️"
                    status_text = "Pending"

                lines.append(f"│ {i:2}. {status_icon} {step_name:<30} │ {status_text[:15]:<15} │")

            lines.append("└" + "─" * 56 + "┘")
            lines.append("")

        # Data transfer statistics
        if transfer_stats := result.get("transfer_stats", {}):
            lines.append("📊 Transfer Statistics:")
            lines.append("─" * 30)
            if bytes_transferred := transfer_stats.get("bytes_transferred"):
                lines.append(f"  📦 Data transferred: {self._format_bytes(bytes_transferred)}")
            if transfer_speed := transfer_stats.get("transfer_speed_mbps"):
                lines.append(f"  ⚡ Transfer speed: {transfer_speed:.1f} MB/s")
            if files_count := transfer_stats.get("files_transferred"):
                lines.append(f"  📁 Files transferred: {files_count}")
            lines.append("")

        # Source and target status
        if source_status := result.get("source_stack_status"):
            lines.append("🔹 Source Stack Status:")
            lines.append(f"   Status: {source_status.get('status', 'unknown')}")
            if source_status.get("stopped"):
                lines.append("   ✅ Successfully stopped for migration")
            if source_status.get("removed") and result.get("remove_source"):
                lines.append("   🗑️  Removed after successful migration")

        if target_status := result.get("target_stack_status"):
            lines.append("")
            lines.append("🔸 Target Stack Status:")
            lines.append(f"   Status: {target_status.get('status', 'unknown')}")
            if target_services := target_status.get("services", []):
                lines.append(f"   Services: {len(target_services)} running")
                for service in target_services[:3]:  # Show first 3 services
                    service_name = service.get("name", "Unknown")
                    service_status = service.get("status", "unknown")
                    lines.append(f"     • {service_name}: {service_status}")
                if len(target_services) > 3:
                    lines.append(f"     ... and {len(target_services) - 3} more services")

        # Migration summary
        lines.append("")
        lines.append("📈 Migration Summary:")
        lines.append("─" * 25)

        total_duration = result.get("total_duration_seconds", 0)
        lines.append(f"  ⏱️  Total duration: {total_duration:.1f} seconds")

        if result.get("dry_run"):
            lines.append("  🧪 Mode: Dry run (no actual changes made)")
        else:
            lines.append("  🚀 Mode: Live migration")

        # Recommendations or next steps
        if recommendations := result.get("recommendations", []):
            lines.append("")
            lines.append("💡 Recommendations:")
            lines.append("─" * 20)
            for rec in recommendations[:3]:  # Show top 3 recommendations
                lines.append(f"  • {rec}")

        lines.append("")
        lines.append("╔" + "═" * 58 + "╗")
        if result.get("overall_success") or result.get("success"):
            lines.append("║ ✅ Migration completed successfully!                    ║")
        else:
            lines.append("║ ❌ Migration failed - check logs for details           ║")
        lines.append("╚" + "═" * 58 + "╝")

        return lines

    def _format_bytes(self, bytes_count: int) -> str:
        """Format bytes count in human-readable format."""
        if bytes_count < 1024:
            return f"{bytes_count} B"
        elif bytes_count < 1024 * 1024:
            return f"{bytes_count / 1024:.1f} KB"
        elif bytes_count < 1024 * 1024 * 1024:
            return f"{bytes_count / (1024 * 1024):.1f} MB"
        else:
            return f"{bytes_count / (1024 * 1024 * 1024):.1f} GB"

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
                message_lines.append("✗ Failed to restart:")
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
