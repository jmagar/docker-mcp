"""
Stack Management Service

Thin facade that delegates to specialized stack management modules.
Provides a clean interface while maintaining backward compatibility.
"""

import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docker_mcp.core.docker_context import DockerContextManager
    from docker_mcp.models.enums import ComposeAction

import structlog
from fastmcp.tools.tool import ToolResult

from docker_mcp.models.enums import ComposeAction

from ..core.config_loader import DockerMCPConfig
from .logs import LogsService
from .stack.migration_orchestrator import StackMigrationOrchestrator
from .stack.operations import StackOperations
from .stack.validation import StackValidation


class StackService:
    """Facade service for Docker Compose stack management operations."""

    def __init__(
        self,
        config: DockerMCPConfig,
        context_manager: "DockerContextManager",
        logs_service: LogsService,
    ):
        self.config = config
        self.context_manager = context_manager
        self.logger = structlog.get_logger()

        # Initialize specialized modules
        self.operations = StackOperations(config, context_manager)
        self.migration_orchestrator = StackMigrationOrchestrator(config, context_manager)
        self.validation = StackValidation()
        self.logs_service = logs_service

    def _validate_host(self, host_id: str) -> tuple[bool, str]:
        """Validate host exists in configuration."""
        if host_id not in self.config.hosts:
            return False, f"Host '{host_id}' not found"
        return True, ""

    def _unwrap(self, result: ToolResult) -> dict[str, Any]:
        """Unwrap ToolResult for consistent structured content access."""
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
            return False, ports, {"success": False, "error": error_msg}

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

    def _format_deploy_result(self, result: dict[str, Any], stack_name: str, host_id: str) -> list[str]:
        """Delegate to operations module for deployment result formatting."""
        return self.operations._format_deploy_result(result, stack_name, host_id)

    def _format_ps_result(self, result: dict[str, Any], stack_name: str) -> list[str]:
        """Delegate to operations module for ps result formatting."""
        return self.operations._format_ps_result(result, stack_name)

    def _format_migrate_result(self, result: dict[str, Any], stack_name: str, source_host: str, target_host: str) -> list[str]:
        """Delegate to operations module for migration result formatting."""
        return self.operations._format_migrate_result(result, stack_name, source_host, target_host)

    # Utility methods that access specialized modules

    async def test_network_connectivity(
        self, source_host_id: str, target_host_id: str
    ) -> tuple[bool, dict]:
        """Test network connectivity between hosts."""
        # Validate both hosts
        for host_id in [source_host_id, target_host_id]:
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return False, {"success": False, "error": error_msg}

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

    async def _validate_compose_file_syntax(self, host_id: str, compose_content: str, environment: dict[str, str] | None = None) -> dict[str, Any]:
        """Validate compose file syntax using docker compose config."""
        import asyncio
        import os
        import tempfile

        from ..utils import build_ssh_command

        try:
            # Validate host exists
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return {
                    "valid": False,
                    "errors": [f"Host validation failed: {error_msg}"],
                    "details": {"host_error": error_msg}
                }

            host = self.config.hosts[host_id]

            # Create temporary files for compose content and environment
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as compose_file:
                compose_file.write(compose_content)
                compose_file_path = compose_file.name

            env_file_path = None
            if environment:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as env_file:
                    for key, value in environment.items():
                        env_file.write(f"{key}={value}\n")
                    env_file_path = env_file.name

            try:
                # Build SSH command to validate compose file on remote host
                ssh_cmd = build_ssh_command(host)

                # Transfer compose file to remote host for validation
                remote_compose_path = f"/tmp/docker-mcp-validate-{os.path.basename(compose_file_path)}"
                remote_env_path = f"/tmp/docker-mcp-validate-{os.path.basename(env_file_path)}" if env_file_path else None

                # Copy compose file to remote host
                copy_cmd = ["scp"]
                if host.port != 22:
                    copy_cmd.extend(["-P", str(host.port)])
                if host.identity_file:
                    copy_cmd.extend(["-i", host.identity_file])
                copy_cmd.extend([compose_file_path, f"{host.user}@{host.hostname}:{remote_compose_path}"])

                copy_process = await asyncio.create_subprocess_exec(
                    *copy_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                _, copy_stderr = await copy_process.communicate()

                if copy_process.returncode != 0:
                    return {
                        "valid": False,
                        "errors": [f"Failed to copy compose file to remote host: {copy_stderr.decode().strip()}"],
                        "details": {"copy_error": copy_stderr.decode().strip()}
                    }

                # Copy environment file if exists
                if env_file_path and remote_env_path:
                    env_copy_cmd = ["scp"]
                    if host.port != 22:
                        env_copy_cmd.extend(["-P", str(host.port)])
                    if host.identity_file:
                        env_copy_cmd.extend(["-i", host.identity_file])
                    env_copy_cmd.extend([env_file_path, f"{host.user}@{host.hostname}:{remote_env_path}"])

                    env_copy_process = await asyncio.create_subprocess_exec(
                        *env_copy_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    await env_copy_process.communicate()

                # Run docker compose config validation on remote host
                validate_cmd = ssh_cmd + [
                    f"cd /tmp && docker compose -f {remote_compose_path}"
                ]
                if remote_env_path:
                    validate_cmd.extend(["--env-file", remote_env_path])
                validate_cmd.extend(["config", "--quiet"])

                validate_process = await asyncio.create_subprocess_exec(
                    *validate_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )

                stdout, stderr = await validate_process.communicate()

                # Clean up remote files
                cleanup_cmd = ssh_cmd + [f"rm -f {remote_compose_path}"]
                if remote_env_path:
                    cleanup_cmd.extend([f"; rm -f {remote_env_path}"])

                cleanup_process = await asyncio.create_subprocess_exec(
                    *cleanup_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                await cleanup_process.communicate()

                if validate_process.returncode == 0:
                    return {
                        "valid": True,
                        "errors": [],
                        "details": {"message": "Compose file syntax is valid"}
                    }
                else:
                    # Parse validation errors for user-friendly messages
                    error_output = stderr.decode().strip()
                    validation_errors = self._parse_compose_validation_errors(error_output)

                    return {
                        "valid": False,
                        "errors": validation_errors,
                        "details": {
                            "raw_error": error_output,
                            "docker_exit_code": validate_process.returncode
                        }
                    }

            finally:
                # Clean up local temporary files
                try:
                    os.unlink(compose_file_path)
                    if env_file_path:
                        os.unlink(env_file_path)
                except OSError:
                    pass  # Files may have already been cleaned up

        except Exception as e:
            return {
                "valid": False,
                "errors": [f"Validation process failed: {str(e)}"],
                "details": {"exception": str(e), "exception_type": type(e).__name__}
            }

    def _parse_compose_validation_errors(self, error_output: str) -> list[str]:
        """Parse docker compose validation errors into user-friendly messages."""
        if not error_output:
            return ["Unknown validation error"]

        errors = []
        lines = error_output.split('\n')

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            # Common Docker Compose error patterns
            if "yaml:" in line.lower():
                errors.append(f"YAML syntax error: {line}")
            elif "invalid" in line.lower() and "service" in line.lower():
                errors.append(f"Invalid service configuration: {line}")
            elif "missing" in line.lower():
                errors.append(f"Missing required field: {line}")
            elif "unknown" in line.lower() and "field" in line.lower():
                errors.append(f"Unknown configuration field: {line}")
            elif "environment variable" in line.lower():
                errors.append(f"Environment variable issue: {line}")
            elif "volume" in line.lower() and ("invalid" in line.lower() or "error" in line.lower()):
                errors.append(f"Volume configuration error: {line}")
            elif "network" in line.lower() and ("invalid" in line.lower() or "error" in line.lower()):
                errors.append(f"Network configuration error: {line}")
            elif "port" in line.lower() and ("invalid" in line.lower() or "error" in line.lower()):
                errors.append(f"Port configuration error: {line}")
            else:
                # Generic error
                errors.append(f"Validation error: {line}")

        # If no specific errors were parsed, return the original output
        if not errors:
            errors.append(f"Docker compose validation failed: {error_output}")

        return errors

    async def handle_action(self, action: ComposeAction | str, **params) -> dict[str, Any]:
        """Unified action handler for all stack operations.

        This method consolidates all dispatcher logic from server.py into the service layer.
        """
        try:
            return await self._dispatch_action(action, **params)
        except Exception as e:
            self.logger.error(
                "stack service action error",
                action=action,
                host_id=params.get("host_id", ""),
                stack_name=params.get("stack_name", ""),
                error=str(e),
            )
            return {
                "success": False,
                "error": f"Service action failed: {str(e)}",
                "action": action,
                "host_id": params.get("host_id", ""),
                "stack_name": params.get("stack_name", ""),
            }

    async def _dispatch_action(self, action: ComposeAction | str, **params) -> dict[str, Any]:
        """Dispatch action to appropriate handler method."""
        # Normalize strings to enum when possible
        normalized_action = self._normalize_action(action)

        # Create dispatch mapping
        dispatch_map: dict[ComposeAction, Callable[..., Awaitable[dict[str, Any]]]] = {
            ComposeAction.LIST: self._handle_list_action,
            ComposeAction.VIEW: self._handle_view_action,
            ComposeAction.DEPLOY: self._handle_deploy_action,
            ComposeAction.LOGS: self._handle_logs_action,
            ComposeAction.DISCOVER: self._handle_discover_action,
            ComposeAction.MIGRATE: self._handle_migrate_action,
            ComposeAction.UP: self._handle_lifecycle_action,
            ComposeAction.DOWN: self._handle_lifecycle_action,
            ComposeAction.RESTART: self._handle_lifecycle_action,
            ComposeAction.BUILD: self._handle_lifecycle_action,
            ComposeAction.PULL: self._handle_lifecycle_action,
            ComposeAction.PS: self._handle_manage_action,
        }

        # Handle string actions that map to manage action
        if normalized_action in ["up", "down", "restart", "build", "pull", "ps"]:
            return await self._handle_manage_action(normalized_action, **params)

        # Dispatch to appropriate handler
        if isinstance(normalized_action, ComposeAction):
            handler = dispatch_map.get(normalized_action)
            if handler:
                if normalized_action in [ComposeAction.UP, ComposeAction.DOWN, ComposeAction.RESTART,
                                       ComposeAction.BUILD, ComposeAction.PULL, ComposeAction.PS]:
                    return await handler(normalized_action, **params)
                return await handler(**params)

        return {
            "success": False,
            "error": f"Unsupported action: {normalized_action.value if hasattr(normalized_action, 'value') else normalized_action}",
            "supported_actions": [a.value for a in ComposeAction],
        }

    def _normalize_action(self, action: ComposeAction | str) -> ComposeAction | str:
        """Normalize action string to enum when possible."""
        if isinstance(action, str):
            try:
                return ComposeAction(action.lower().strip())
            except ValueError:
                return action.lower().strip()
        return action

    def _error_response(self, message: str, **extra: Any) -> dict[str, Any]:
        formatted_text = f"❌ {message}"
        response: dict[str, Any] = {
            "success": False,
            "error": message,
            "formatted_output": formatted_text,
        }
        response.update(extra)
        return response

    async def _handle_list_action(self, **params) -> dict[str, Any]:
        """Handle LIST action."""
        host_id = params.get("host_id", "")
        if not host_id:
            return self._error_response("host_id is required for list action")

        result = await self.list_stacks(host_id)
        return self._unwrap(result)

    async def _handle_view_action(self, **params) -> dict[str, Any]:
        """Handle VIEW action."""
        host_id = params.get("host_id", "")
        stack_name = params.get("stack_name", "")

        if not host_id:
            return self._error_response("host_id is required for view action")
        if not stack_name:
            return self._error_response("stack_name is required for view action")

        result = await self.get_stack_compose_file(host_id, stack_name)
        return self._unwrap(result)

    async def _handle_deploy_action(self, **params) -> dict[str, Any]:
        """Handle DEPLOY action."""

        host_id = params.get("host_id", "")
        stack_name = params.get("stack_name", "")
        compose_content = params.get("compose_content", "")
        environment = params.get("environment", {})
        pull_images = params.get("pull_images", True)
        recreate = params.get("recreate", False)

        if not host_id:
            return self._error_response("host_id is required for deploy action")
        if not stack_name:
            return self._error_response("stack_name is required for deploy action")
        if not compose_content:
            return self._error_response("compose_content is required for deploy action")

        # Validate stack name format (allow underscores per IMPLEMENT_ME.md)
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", stack_name):
            return self._error_response(
                "stack_name must contain only letters, numbers, underscores, and hyphens, starting with alphanumeric"
            )

        # Validate stack name length and reserved names
        if len(stack_name) > 63:
            return self._error_response("stack_name must be 63 characters or fewer")

        reserved_names = {"docker", "compose", "system", "network", "volume"}
        if stack_name.lower() in reserved_names:
            return self._error_response(f"stack_name '{stack_name}' is reserved")

        # Validate compose file syntax before deployment
        validation_result = await self._validate_compose_file_syntax(host_id, compose_content, environment)
        if not validation_result["valid"]:
            return {
                "success": False,
                "error": "Compose file validation failed",
                "validation_errors": validation_result["errors"],
                "validation_details": validation_result.get("details", {}),
                "formatted_output": "❌ Compose file validation failed",
            }

        result = await self.deploy_stack(
            host_id, stack_name, compose_content, environment, pull_images, recreate
        )
        return self._unwrap(result)

    async def _handle_manage_action(self, action: ComposeAction | str, **params) -> dict[str, Any]:
        """Handle string-based manage actions."""
        # Convert enum to string if needed
        action_str = action.value if hasattr(action, "value") else str(action)

        host_id = params.get("host_id", "")
        stack_name = params.get("stack_name", "")
        options = params.get("options", {})

        if not host_id:
            return self._error_response(f"host_id is required for {action_str} action")
        if not stack_name:
            return self._error_response(f"stack_name is required for {action_str} action")

        result = await self.manage_stack(host_id, stack_name, action_str, options)
        return self._unwrap(result)

    async def _handle_logs_action(self, **params) -> dict[str, Any]:
        """Handle LOGS action."""
        host_id = params.get("host_id", "")
        stack_name = params.get("stack_name", "")
        follow = params.get("follow", False)
        lines = params.get("lines", 100)

        if not host_id:
            return self._error_response("host_id is required for logs action")
        if not stack_name:
            return self._error_response("stack_name is required for logs action")
        if lines < 1 or lines > 10000:
            return self._error_response("lines must be between 1 and 10000")

        try:
            if host_id not in self.config.hosts:
                return {"success": False, "error": f"Host {host_id} not found"}

            logs_options = {"tail": str(lines), "follow": follow}
            result = await self.manage_stack(host_id, stack_name, "logs", logs_options)

            logs_data = self._unwrap(result)
            if logs_data.get("success", False):
                if "output" in logs_data:
                    logs_lines = logs_data["output"].split("\n") if logs_data["output"] else []
                    header = f"Stack Logs: {stack_name} on {host_id} ({len(logs_lines)} lines)"
                    formatted_lines = [header]
                    if logs_lines:
                        formatted_lines.append("")
                        formatted_lines.extend(logs_lines)
                    formatted_text = "\n".join(formatted_lines)
                    return {
                        "success": True,
                        "host_id": host_id,
                        "stack_name": stack_name,
                        "logs": logs_lines,
                        "lines_requested": lines,
                        "lines_returned": len(logs_lines),
                        "follow": follow,
                        "formatted_output": formatted_text,
                    }
                logs_data.setdefault("formatted_output", "❌ Failed to retrieve stack logs")
                return logs_data
            return self._error_response("Failed to retrieve stack logs")
        except Exception as e:
            self.logger.error(
                "stack logs error", host_id=host_id, stack_name=stack_name, error=str(e)
            )
            return self._error_response(f"Failed to get stack logs: {str(e)}")

    async def _handle_discover_action(self, **params) -> dict[str, Any]:
        """Handle DISCOVER action."""
        host_id = params.get("host_id", "")

        if not host_id:
            return {"success": False, "error": "host_id is required for discover action"}

        try:
            compose_manager = self.operations.stack_tools.compose_manager
            discovery = await compose_manager.discover_compose_locations(host_id)
            return {"success": True, "host_id": host_id, "compose_discovery": discovery}
        except Exception as e:
            self.logger.error("compose discover error", host_id=host_id, error=str(e))
            return {
                "success": False,
                "error": f"Failed to discover compose paths: {str(e)}",
                "host_id": host_id,
            }

    async def _handle_migrate_action(self, **params) -> dict[str, Any]:
        """Handle MIGRATE action."""
        host_id = params.get("host_id", "")
        target_host_id = params.get("target_host_id", "")
        stack_name = params.get("stack_name", "")
        skip_stop_source = params.get("skip_stop_source", False)
        start_target = params.get("start_target", True)
        remove_source = params.get("remove_source", False)
        dry_run = params.get("dry_run", False)

        if not host_id:
            return {"success": False, "error": "host_id (source) is required for migrate action"}
        if not target_host_id:
            return {"success": False, "error": "target_host_id is required for migrate action"}
        if not stack_name:
            return {"success": False, "error": "stack_name is required for migrate action"}

        result = await self.migrate_stack(
            source_host_id=host_id,
            target_host_id=target_host_id,
            stack_name=stack_name,
            skip_stop_source=skip_stop_source,
            start_target=start_target,
            remove_source=remove_source,
            dry_run=dry_run,
        )

        migration_result = self._unwrap(result)
        if migration_result.get("success", False):
            # Create a copy to avoid modifying the original
            migration_result = migration_result.copy()
            if "overall_success" in migration_result:
                migration_result["success"] = migration_result["overall_success"]
            return migration_result
        return migration_result

    async def _handle_lifecycle_action(self, action, **params) -> dict[str, Any]:
        """Handle ComposeAction enum lifecycle actions."""
        host_id = params.get("host_id", "")
        stack_name = params.get("stack_name", "")
        options = params.get("options", {})

        if not host_id:
            return {"success": False, "error": "host_id is required for stack lifecycle actions"}
        if not stack_name:
            return {"success": False, "error": "stack_name is required for stack lifecycle actions"}

        result = await self.manage_stack(
            host_id=host_id, stack_name=stack_name, action=action.value, options=options
        )
        return self._unwrap(result)
