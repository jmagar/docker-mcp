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
        # Validate host exists
        is_valid, error_msg = self._validate_host(host_id)
        if not is_valid:
            return {
                "valid": False,
                "errors": [f"Host validation failed: {error_msg}"],
                "details": {"host_error": error_msg}
            }

        try:
            # Create temporary files and perform validation
            return await self._perform_remote_compose_validation(host_id, compose_content, environment)
        except Exception as e:
            return {
                "valid": False,
                "errors": [f"Validation process failed: {str(e)}"],
                "details": {"exception": str(e), "exception_type": type(e).__name__}
            }

    async def _perform_remote_compose_validation(self, host_id: str, compose_content: str, environment: dict[str, str] | None) -> dict[str, Any]:
        """Perform compose validation on remote host."""
        host = self.config.hosts[host_id]

        # Create temporary files for compose content and environment
        temp_files = await self._create_temp_files(compose_content, environment)

        try:
            # Transfer files to remote host and validate
            remote_paths = await self._transfer_files_to_remote_host(host, temp_files)
            validation_result = await self._execute_remote_validation(host, remote_paths)
            await self._cleanup_remote_files(host, remote_paths)

            return validation_result
        finally:
            # Clean up local temporary files
            self._cleanup_local_temp_files(temp_files)

    async def _create_temp_files(self, compose_content: str, environment: dict[str, str] | None) -> dict[str, str]:
        """Create temporary files for compose content and environment."""
        import tempfile

        temp_files = {}

        # Create compose file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as compose_file:
            compose_file.write(compose_content)
            temp_files['compose'] = compose_file.name

        # Create environment file if needed
        if environment:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as env_file:
                for key, value in environment.items():
                    env_file.write(f"{key}={value}\n")
                temp_files['env'] = env_file.name

        return temp_files

    async def _transfer_files_to_remote_host(self, host, temp_files: dict[str, str]) -> dict[str, str]:
        """Transfer temporary files to remote host for validation."""
        import os

        remote_paths = {
            'compose': f"/tmp/docker-mcp-validate-{os.path.basename(temp_files['compose'])}"  # noqa: S108 - Using /tmp with unique identifiers for compose validation only
        }

        if 'env' in temp_files:
            remote_paths['env'] = f"/tmp/docker-mcp-validate-{os.path.basename(temp_files['env'])}"  # noqa: S108 - Using /tmp with unique identifiers for compose validation only

        # Copy compose file to remote host
        copy_result = await self._copy_file_to_remote(host, temp_files['compose'], remote_paths['compose'])
        if not copy_result['success']:
            raise Exception(f"Failed to copy compose file: {copy_result['error']}")

        # Copy environment file if exists
        if 'env' in temp_files:
            await self._copy_file_to_remote(host, temp_files['env'], remote_paths['env'])

        return remote_paths

    async def _copy_file_to_remote(self, host, local_path: str, remote_path: str) -> dict[str, Any]:
        """Copy a single file to remote host via SCP."""
        import asyncio

        copy_cmd = ["scp"]
        if host.port != 22:
            copy_cmd.extend(["-P", str(host.port)])
        if host.identity_file:
            copy_cmd.extend(["-i", host.identity_file])
        copy_cmd.extend([local_path, f"{host.user}@{host.hostname}:{remote_path}"])

        copy_process = await asyncio.create_subprocess_exec(
            *copy_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, copy_stderr = await copy_process.communicate()

        if copy_process.returncode != 0:
            return {
                "success": False,
                "error": copy_stderr.decode().strip()
            }

        return {"success": True}

    async def _execute_remote_validation(self, host, remote_paths: dict[str, str]) -> dict[str, Any]:
        """Execute docker compose config validation on remote host."""
        import asyncio

        from ..utils import build_ssh_command

        # Build validation command
        ssh_cmd = build_ssh_command(host)
        validate_cmd = ssh_cmd + [
            f"cd /tmp && docker compose -f {remote_paths['compose']}"
        ]

        if 'env' in remote_paths:
            validate_cmd.extend(["--env-file", remote_paths['env']])
        validate_cmd.extend(["config", "--quiet"])

        # Execute validation
        validate_process = await asyncio.create_subprocess_exec(
            *validate_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await validate_process.communicate()

        if validate_process.returncode == 0:
            return {
                "valid": True,
                "errors": [],
                "details": {"message": "Compose file syntax is valid"}
            }
        else:
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

    async def _cleanup_remote_files(self, host, remote_paths: dict[str, str]) -> None:
        """Clean up temporary files on remote host."""
        import asyncio

        from ..utils import build_ssh_command

        ssh_cmd = build_ssh_command(host)
        cleanup_cmd = ssh_cmd + [f"rm -f {remote_paths['compose']}"]

        if 'env' in remote_paths:
            cleanup_cmd.extend([f"; rm -f {remote_paths['env']}"])

        cleanup_process = await asyncio.create_subprocess_exec(
            *cleanup_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await cleanup_process.communicate()

    def _cleanup_local_temp_files(self, temp_files: dict[str, str]) -> None:
        """Clean up local temporary files."""
        import os

        for file_path in temp_files.values():
            try:
                os.unlink(file_path)
            except OSError:
                pass  # Files may have already been cleaned up

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

            # Parse error line using helper method
            parsed_error = self._parse_single_error_line(line)
            errors.append(parsed_error)

        # If no specific errors were parsed, return the original output
        if not errors:
            errors.append(f"Docker compose validation failed: {error_output}")

        return errors

    def _parse_single_error_line(self, line: str) -> str:
        """Parse a single error line and categorize it."""
        line_lower = line.lower()

        # Define error patterns and their categories
        error_patterns = [
            ("yaml:", "YAML syntax error"),
            (["invalid", "service"], "Invalid service configuration"),
            ("missing", "Missing required field"),
            (["unknown", "field"], "Unknown configuration field"),
            ("environment variable", "Environment variable issue"),
        ]

        # Check for specific patterns
        for pattern, category in error_patterns:
            if self._line_matches_pattern(line_lower, pattern):
                return f"{category}: {line}"

        # Check for resource-specific errors
        resource_error = self._parse_resource_error(line_lower, line)
        if resource_error:
            return resource_error

        # Generic error
        return f"Validation error: {line}"

    def _line_matches_pattern(self, line_lower: str, pattern) -> bool:
        """Check if line matches a pattern (string or list of strings)."""
        if isinstance(pattern, str):
            return pattern in line_lower
        elif isinstance(pattern, list):
            return all(term in line_lower for term in pattern)
        return False

    def _parse_resource_error(self, line_lower: str, line: str) -> str | None:
        """Parse resource-specific errors (volume, network, port)."""
        resource_errors = [
            ("volume", "Volume configuration error"),
            ("network", "Network configuration error"),
            ("port", "Port configuration error"),
        ]

        for resource, error_type in resource_errors:
            if resource in line_lower and ("invalid" in line_lower or "error" in line_lower):
                return f"{error_type}: {line}"

        return None

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
