"""
Stack Management Service

Thin facade that delegates to specialized stack management modules.
Provides a clean interface while maintaining backward compatibility.
"""

from typing import Any

import structlog
from fastmcp.tools.tool import ToolResult

from ..core.config_loader import DockerMCPConfig
from ..core.docker_context import DockerContextManager
from .stack.migration_orchestrator import StackMigrationOrchestrator
from .stack.operations import StackOperations
from .stack.validation import StackValidation
from .logs import LogsService


class StackService:
    """Facade service for Docker Compose stack management operations."""

    def __init__(
        self,
        config: DockerMCPConfig,
        context_manager: DockerContextManager,
        logs_service: LogsService | None = None,
    ):
        self.config = config
        self.context_manager = context_manager
        self.logger = structlog.get_logger()

        # Initialize specialized modules
        self.operations = StackOperations(config, context_manager)
        self.migration_orchestrator = StackMigrationOrchestrator(config, context_manager)
        self.validation = StackValidation()
        self.logs_service = logs_service or LogsService(config, context_manager)

    def _validate_host(self, host_id: str) -> tuple[bool, str]:
        """Validate host exists in configuration."""
        if host_id not in self.config.hosts:
            return False, f"Host '{host_id}' not found"
        return True, ""

    # Core Operations - Delegate to StackOperations

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
        return await self.operations.deploy_stack(
            host_id, stack_name, compose_content, environment, pull_images, recreate
        )

    async def manage_stack(
        self, host_id: str, stack_name: str, action: str, options: dict[str, Any] | None = None
    ) -> ToolResult:
        """Unified stack lifecycle management."""
        return await self.operations.manage_stack(host_id, stack_name, action, options)

    async def list_stacks(self, host_id: str) -> ToolResult:
        """List Docker Compose stacks on a host."""
        return await self.operations.list_stacks(host_id)

    async def get_stack_compose_file(self, host_id: str, stack_name: str) -> ToolResult:
        """Get the docker-compose.yml content for a specific stack."""
        return await self.operations.get_stack_compose_file(host_id, stack_name)

    # Migration Operations - Delegate to StackMigrationOrchestrator

    async def migrate_stack(
        self,
        source_host_id: str,
        target_host_id: str,
        stack_name: str,
        skip_stop_source: bool = False,
        start_target: bool = True,
        remove_source: bool = False,
        dry_run: bool = False,
    ) -> ToolResult:
        """Migrate a Docker Compose stack between hosts with data integrity protection."""
        return await self.migration_orchestrator.migrate_stack(
            source_host_id=source_host_id,
            target_host_id=target_host_id,
            stack_name=stack_name,
            skip_stop_source=skip_stop_source,
            start_target=start_target,
            remove_source=remove_source,
            dry_run=dry_run,
        )

    # Validation Operations - Direct access for backward compatibility

    def validate_compose_syntax(
        self, compose_content: str, stack_name: str
    ) -> tuple[bool, list[str], dict]:
        """Validate Docker Compose file syntax and configuration."""
        return self.validation.validate_compose_syntax(compose_content, stack_name)

    async def check_disk_space(self, host_id: str, estimated_size: int) -> tuple[bool, str, dict]:
        """Check if target host has sufficient disk space."""
        is_valid, error_msg = self._validate_host(host_id)
        if not is_valid:
            return False, error_msg, {}

        host = self.config.hosts[host_id]
        return await self.validation.check_disk_space(host, estimated_size)

    async def check_tool_availability(
        self, host_id: str, tools: list[str]
    ) -> tuple[bool, list[str], dict]:
        """Check if required tools are available on host."""
        is_valid, error_msg = self._validate_host(host_id)
        if not is_valid:
            return False, [f"Host validation failed: {error_msg}"], {}

        host = self.config.hosts[host_id]
        return await self.validation.check_tool_availability(host, tools)

    def extract_ports_from_compose(self, compose_content: str) -> list[int]:
        """Extract exposed ports from compose file."""
        return self.validation.extract_ports_from_compose(compose_content)

    async def check_port_conflicts(
        self, host_id: str, ports: list[int]
    ) -> tuple[bool, list[int], dict]:
        """Check if ports are already in use on host."""
        is_valid, error_msg = self._validate_host(host_id)
        if not is_valid:
            return False, ports, {"error": error_msg}

        host = self.config.hosts[host_id]
        return await self.validation.check_port_conflicts(host, ports)

    def extract_names_from_compose(self, compose_content: str) -> tuple[list[str], list[str]]:
        """Extract service and network names from compose file."""
        return self.validation.extract_names_from_compose(compose_content)

    async def check_name_conflicts(
        self, host_id: str, service_names: list[str], network_names: list[str]
    ) -> tuple[bool, list[str], dict]:
        """Check for container and network name conflicts."""
        is_valid, error_msg = self._validate_host(host_id)
        if not is_valid:
            return False, [f"Host validation failed: {error_msg}"], {}

        host = self.config.hosts[host_id]
        return await self.validation.check_name_conflicts(host, service_names, network_names)

    # Legacy method aliases for backward compatibility

    def _format_stack_action_result(
        self, result: dict[str, Any], stack_name: str, action: str
    ) -> list[str]:
        """Legacy method - delegate to operations module."""
        return self.operations._format_stack_action_result(result, stack_name, action)

    def _format_stacks_list(self, result: dict[str, Any], host_id: str) -> list[str]:
        """Legacy method - delegate to operations module."""
        return self.operations._format_stacks_list(result, host_id)

    # Utility methods that access specialized modules

    async def test_network_connectivity(
        self, source_host_id: str, target_host_id: str
    ) -> tuple[bool, dict]:
        """Test network connectivity between hosts."""
        # Validate both hosts
        for host_id in [source_host_id, target_host_id]:
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return False, {"error": error_msg}

        source_host = self.config.hosts[source_host_id]
        target_host = self.config.hosts[target_host_id]

        # Use network module
        from .stack import StackNetwork

        network = StackNetwork()
        return await network.test_network_connectivity(source_host, target_host)

    def assess_migration_risks(
        self,
        stack_name: str,
        data_size_bytes: int,
        estimated_downtime: float,
        source_inventory: dict = None,
        compose_content: str = "",
    ) -> dict:
        """Assess risks associated with migration."""
        from .stack import StackRiskAssessment

        risk_assessment = StackRiskAssessment()
        return risk_assessment.assess_migration_risks(
            stack_name, data_size_bytes, estimated_downtime, source_inventory, compose_content
        )

    def extract_expected_mounts(
        self, compose_content: str, target_appdata: str, stack_name: str
    ) -> list[str]:
        """Extract expected volume mounts from compose file."""
        from .stack import StackVolumeUtils

        volume_utils = StackVolumeUtils()
        return volume_utils.extract_expected_mounts(compose_content, target_appdata, stack_name)

    def normalize_volume_entry(
        self, volume: Any, target_appdata: str, stack_name: str
    ) -> str | None:
        """Normalize a single volume entry to source:destination format."""
        from .stack import StackVolumeUtils

        volume_utils = StackVolumeUtils()
        return volume_utils.normalize_volume_entry(volume, target_appdata, stack_name)

    async def handle_action(self, action, **params) -> dict[str, Any]:
        """Unified action handler for all stack operations.

        This method consolidates all dispatcher logic from server.py into the service layer.
        """
        import re

        try:
            # Import dependencies for this handler
            from ..models.enums import ComposeAction

            # Extract common parameters
            host_id = params.get("host_id", "")
            stack_name = params.get("stack_name", "")
            compose_content = params.get("compose_content", "")
            environment = params.get("environment", {})
            pull_images = params.get("pull_images", True)
            recreate = params.get("recreate", False)
            follow = params.get("follow", False)
            lines = params.get("lines", 100)
            dry_run = params.get("dry_run", False)
            options = params.get("options", {})
            target_host_id = params.get("target_host_id", "")
            remove_source = params.get("remove_source", False)
            skip_stop_source = params.get("skip_stop_source", False)
            start_target = params.get("start_target", True)

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

                result = await self.get_stack_compose_file(host_id, stack_name)
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

                result = await self.manage_stack(host_id, stack_name, action, options)
                # Convert ToolResult to dict for consistency
                if hasattr(result, "structured_content"):
                    return result.structured_content or {
                        "success": True,
                        "data": "No structured content",
                    }
                return result

            elif action == ComposeAction.LOGS:
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

                    result = await self.manage_stack(host_id, stack_name, "logs", logs_options)

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
                        "stack logs error",
                        host_id=host_id,
                        stack_name=stack_name,
                        error=str(e),
                    )
                    return {"success": False, "error": f"Failed to get stack logs: {str(e)}"}

            elif action == ComposeAction.DISCOVER:
                # Validate required parameters for discover action
                if not host_id:
                    return {"success": False, "error": "host_id is required for discover action"}

                try:
                    # Use ComposeManager via operations -> stack_tools
                    compose_manager = self.operations.stack_tools.compose_manager
                    discovery = await compose_manager.discover_compose_locations(host_id)

                    return {
                        "success": True,
                        "host_id": host_id,
                        "compose_discovery": discovery,
                    }
                except Exception as e:
                    self.logger.error(
                        "compose discover error", host_id=host_id, error=str(e)
                    )
                    return {
                        "success": False,
                        "error": f"Failed to discover compose paths: {str(e)}",
                        "host_id": host_id,
                    }

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
                result = await self.migrate_stack(
                    source_host_id=host_id,
                    target_host_id=target_host_id,
                    stack_name=stack_name,
                    skip_stop_source=skip_stop_source,
                    start_target=start_target,
                    remove_source=remove_source,
                    dry_run=dry_run,
                )

                # Convert ToolResult to dict for consistency
                if hasattr(result, "structured_content") and result.structured_content:
                    migration_result = result.structured_content.copy()
                    # Map 'overall_success' to 'success' for consistency with other actions
                    if "overall_success" in migration_result:
                        migration_result["success"] = migration_result["overall_success"]
                    return migration_result
                return {"success": True, "data": "Migration completed"}

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
                result = await self.manage_stack(
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
                    "error": f"Unsupported action: {action.value if hasattr(action, 'value') else action}",
                    "supported_actions": [a.value for a in ComposeAction],
                }

        except Exception as e:
            self.logger.error(
                "stack service action error",
                action=action,
                host_id=host_id,
                stack_name=stack_name,
                error=str(e),
            )
            return {
                "success": False,
                "error": f"Service action failed: {str(e)}",
                "action": action,
                "host_id": host_id,
                "stack_name": stack_name,
            }
