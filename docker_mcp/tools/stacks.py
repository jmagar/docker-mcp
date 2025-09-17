"""Stack deployment MCP tools."""

import asyncio
import json
import shlex
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import docker
import structlog

from ..constants import DOCKER_COMPOSE_PROJECT, DOCKER_COMPOSE_SERVICE
from ..core.compose_manager import ComposeManager
from ..core.config_loader import DockerMCPConfig
from ..core.docker_context import DockerContextManager
from ..core.exceptions import DockerCommandError, DockerContextError
from ..models.container import StackInfo
from ..utils import build_ssh_command

logger = structlog.get_logger()


class StackTools:
    """Stack deployment tools for MCP."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager
        self.compose_manager = ComposeManager(config, context_manager)

    async def deploy_stack(
        self,
        host_id: str,
        stack_name: str,
        compose_content: str,
        environment: dict[str, str] | None = None,
        pull_images: bool = True,
        recreate: bool = False,
    ) -> dict[str, Any]:
        """Deploy a Docker Compose stack to a remote host with persistent compose files.

        Args:
            host_id: ID of the Docker host
            stack_name: Name for the stack (used as project name)
            compose_content: Docker Compose YAML content
            environment: Environment variables for the stack
            pull_images: Pull latest images before deploying
            recreate: Recreate containers even if config hasn't changed

        Returns:
            Deployment result
        """
        try:
            # Validate stack name
            if not self._validate_stack_name(stack_name):
                return {
                    "success": False,
                    "error": f"Invalid stack name: {stack_name}. Must be alphanumeric with hyphens/underscores.",
                    "host_id": host_id,
                    "timestamp": datetime.now().isoformat(),
                }

            # Write compose file to persistent location on remote host
            compose_file_path = await self.compose_manager.write_compose_file(
                host_id, stack_name, compose_content
            )

            # Deploy using persistent compose file
            result = await self._deploy_stack_with_persistent_file(
                host_id, stack_name, compose_file_path, environment or {}, pull_images, recreate
            )

            logger.info(
                "Stack deployment completed",
                host_id=host_id,
                stack_name=stack_name,
                success=result["success"],
            )

            return result

        except Exception as e:
            logger.error(
                "Stack deployment failed", host_id=host_id, stack_name=stack_name, error=str(e)
            )
            return {
                "success": False,
                "error": str(e),
                "host_id": host_id,
                "stack_name": stack_name,
                "timestamp": datetime.now().isoformat(),
            }

    async def list_stacks(self, host_id: str) -> dict[str, Any]:
        """List Docker Compose stacks on a host.

        Args:
            host_id: ID of the Docker host

        Returns:
            List of stacks
        """
        try:
            client = await self.context_manager.get_client(host_id)
            if client is None:
                return {"success": False, "error": f"Could not connect to Docker on host {host_id}"}

            # Get all containers and group by compose project using Docker SDK
            containers = await asyncio.to_thread(client.containers.list, all=True)

            # Group containers by compose project
            projects = defaultdict(list)
            for container in containers:
                labels = container.labels or {}
                project_name = labels.get(DOCKER_COMPOSE_PROJECT)
                if project_name:
                    service_name = labels.get(DOCKER_COMPOSE_SERVICE, "")
                    projects[project_name].append(
                        {
                            "container": container,
                            "service": service_name,
                            "status": container.status,
                            "created": container.attrs.get("Created", ""),
                        }
                    )

            # Convert to StackInfo objects
            stacks = []
            for project_name, project_containers in projects.items():
                # Determine overall project status
                statuses = [c["status"] for c in project_containers]
                if all(s == "running" for s in statuses):
                    project_status = "running"
                elif any(s == "running" for s in statuses):
                    project_status = "partial"
                else:
                    project_status = "stopped"

                # Get unique services and timestamps
                services = list(set(c["service"] for c in project_containers if c["service"]))
                created_times = [c["created"] for c in project_containers if c["created"]]

                # Convert created times to datetime objects
                created_datetimes = []
                for created_str in created_times:
                    if created_str and isinstance(created_str, str):
                        try:
                            # Parse ISO format timestamp (Docker API format)
                            # Handle both with and without microseconds
                            if "." in created_str:
                                created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                            else:
                                created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                            created_datetimes.append(created_dt)
                        except (ValueError, TypeError):
                            # Skip invalid timestamp formats
                            continue

                created = min(created_datetimes) if created_datetimes else None

                stack = StackInfo(
                    name=project_name,
                    host_id=host_id,
                    services=services,
                    status=project_status,
                    created=created,
                    updated=created,  # Docker SDK doesn't provide separate updated time
                )
                stacks.append(stack.model_dump())

            logger.info("Listed stacks", host_id=host_id, count=len(stacks))
            return {
                "success": True,
                "stacks": stacks,
                "host_id": host_id,
                "timestamp": datetime.now().isoformat(),
            }

        except docker.errors.APIError as e:
            logger.error("Docker API error listing stacks", host_id=host_id, error=str(e))
            return {
                "success": False,
                "error": f"Docker API error: {str(e)}",
                "host_id": host_id,
                "timestamp": datetime.now().isoformat(),
            }
        except (DockerCommandError, DockerContextError) as e:
            logger.error("Failed to list stacks", host_id=host_id, error=str(e))
            return {
                "success": False,
                "error": str(e),
                "host_id": host_id,
                "timestamp": datetime.now().isoformat(),
            }

    async def stop_stack(self, host_id: str, stack_name: str) -> dict[str, Any]:
        """Stop a Docker Compose stack.

        Args:
            host_id: ID of the Docker host
            stack_name: Name of the stack to stop

        Returns:
            Operation result
        """
        try:
            if not self._validate_stack_name(stack_name):
                return {
                    "success": False,
                    "error": f"Invalid stack name: {stack_name}",
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "timestamp": datetime.now().isoformat(),
                }
            cmd = f"compose --project-name {stack_name} stop"
            await self.context_manager.execute_docker_command(host_id, cmd)

            logger.info("Stack stopped", host_id=host_id, stack_name=stack_name)
            return {
                "success": True,
                "message": f"Stack {stack_name} stopped successfully",
                "host_id": host_id,
                "stack_name": stack_name,
                "timestamp": datetime.now().isoformat(),
            }

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to stop stack", host_id=host_id, stack_name=stack_name, error=str(e)
            )
            return {
                "success": False,
                "error": str(e),
                "host_id": host_id,
                "stack_name": stack_name,
                "timestamp": datetime.now().isoformat(),
            }

    async def remove_stack(
        self, host_id: str, stack_name: str, remove_volumes: bool = False
    ) -> dict[str, Any]:
        """Remove a Docker Compose stack.

        Args:
            host_id: ID of the Docker host
            stack_name: Name of the stack to remove
            remove_volumes: Also remove associated volumes

        Returns:
            Operation result
        """
        try:
            if not self._validate_stack_name(stack_name):
                return {
                    "success": False,
                    "error": f"Invalid stack name: {stack_name}",
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "timestamp": datetime.now().isoformat(),
                }
            cmd = f"compose --project-name {stack_name} down"
            if remove_volumes:
                cmd += " --volumes"

            await self.context_manager.execute_docker_command(host_id, cmd)

            logger.info(
                "Stack removed",
                host_id=host_id,
                stack_name=stack_name,
                remove_volumes=remove_volumes,
            )
            return {
                "success": True,
                "message": f"Stack {stack_name} removed successfully",
                "host_id": host_id,
                "stack_name": stack_name,
                "removed_volumes": remove_volumes,
                "timestamp": datetime.now().isoformat(),
            }

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to remove stack", host_id=host_id, stack_name=stack_name, error=str(e)
            )
            return {
                "success": False,
                "error": str(e),
                "host_id": host_id,
                "stack_name": stack_name,
                "timestamp": datetime.now().isoformat(),
            }

    async def _deploy_stack_with_persistent_file(
        self,
        host_id: str,
        stack_name: str,
        compose_file_path: str,
        environment: dict[str, str],
        pull_images: bool,
        recreate: bool,
    ) -> dict[str, Any]:
        """Deploy stack using Docker context with persistent compose file."""
        try:
            # Get Docker context for the host
            context_name = await self.context_manager.ensure_context(host_id)

            # Pull images first if requested
            if pull_images:
                try:
                    await self._execute_compose_with_file(
                        context_name, stack_name, compose_file_path, ["pull"], environment
                    )
                    logger.info(
                        "Images pulled successfully", host_id=host_id, stack_name=stack_name
                    )
                except Exception as e:
                    logger.warning(
                        "Image pull failed, continuing with deployment",
                        host_id=host_id,
                        stack_name=stack_name,
                        error=str(e),
                    )

            # Build deployment command arguments
            up_args = ["up", "-d"]
            if recreate:
                up_args.append("--force-recreate")

            # Deploy the stack
            result = await self._execute_compose_with_file(
                context_name, stack_name, compose_file_path, up_args, environment
            )

            logger.info(
                "Stack deployed successfully",
                host_id=host_id,
                stack_name=stack_name,
                compose_file=compose_file_path,
            )

            return {
                "success": True,
                "message": f"Stack {stack_name} deployed successfully",
                "output": result,
                "host_id": host_id,
                "stack_name": stack_name,
                "compose_file": compose_file_path,
                "timestamp": datetime.now().isoformat(),
            }

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Stack deployment failed", host_id=host_id, stack_name=stack_name, error=str(e)
            )
            return {
                "success": False,
                "error": str(e),
                "host_id": host_id,
                "stack_name": stack_name,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(
                "Unexpected deployment error", host_id=host_id, stack_name=stack_name, error=str(e)
            )
            return {
                "success": False,
                "error": f"Deployment failed: {e}",
                "host_id": host_id,
                "stack_name": stack_name,
                "timestamp": datetime.now().isoformat(),
            }

    async def _execute_compose_with_file(
        self,
        context_name: str,
        project_name: str,
        compose_file_path: str,
        compose_args: list[str],
        environment: dict[str, str] | None = None,
        timeout: int = 300,
    ) -> str:
        """Execute docker compose command via SSH on remote host."""
        # Extract directory from full path
        compose_path = Path(compose_file_path)
        project_directory = str(compose_path.parent)

        # Build docker compose command to run on remote host
        # Since we cd into the project directory, use relative path for compose file
        compose_filename = compose_path.name
        compose_cmd = [
            "docker",
            "compose",
            "--project-name",
            project_name,
            "-f",
            compose_filename,
        ]
        compose_cmd.extend(compose_args)

        # Get host config for SSH connection
        host_id = self._extract_host_id_from_context(context_name)
        host_config = self.config.hosts.get(host_id)
        if not host_config:
            raise DockerCommandError(f"Host {host_id} not found in configuration")

        # Build SSH command
        ssh_cmd = build_ssh_command(host_config)

        # Build remote command
        remote_cmd = self._build_remote_command(project_directory, compose_cmd, environment)

        ssh_cmd.append(remote_cmd)

        # Record start time and log operation start
        start_time = time.monotonic()
        compose_action = " ".join(compose_cmd)

        logger.info(
            "SSH compose operation started",
            host_id=host_id,
            ssh_host=host_config.hostname,
            compose_action=compose_action,  # Safe to log
            # Note: remote_command may contain env vars with secrets - not logged
        )

        try:
            result = await asyncio.to_thread(
                subprocess.run,  # nosec B603
                ssh_cmd,
                check=False,
                text=True,
                capture_output=True,
                timeout=timeout,
            )

            # Calculate duration and log completion
            duration = time.monotonic() - start_time

            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip()
                logger.error(
                    "SSH compose operation failed",
                    host_id=host_id,
                    ssh_host=host_config.hostname,
                    compose_action=compose_action,
                    duration=duration,
                    return_code=result.returncode,
                    error_message=error_msg
                )
                raise DockerCommandError(f"Docker compose command failed: {error_msg}")

            logger.info(
                "SSH compose operation completed",
                host_id=host_id,
                ssh_host=host_config.hostname,
                compose_action=compose_action,
                duration=duration
            )
            return result.stdout.strip()

        except subprocess.TimeoutExpired as e:
            duration = time.monotonic() - start_time
            logger.error(
                "SSH compose operation timed out",
                host_id=host_id,
                ssh_host=host_config.hostname,
                compose_action=compose_action,
                duration=duration,
                timeout=timeout
            )
            raise DockerCommandError(f"Docker compose command timed out: {e}") from e
        except Exception as e:
            duration = time.monotonic() - start_time
            logger.error(
                "SSH compose operation failed with exception",
                host_id=host_id,
                ssh_host=host_config.hostname,
                compose_action=compose_action,
                duration=duration,
                error=str(e)
            )
            if isinstance(e, DockerCommandError):
                raise
            raise DockerCommandError(f"Failed to execute docker compose: {e}") from e

    def _extract_host_id_from_context(self, context_name: str) -> str:
        """Extract host_id from context_name."""
        return (
            context_name.replace("docker-mcp-", "")
            if context_name.startswith("docker-mcp-")
            else context_name
        )

    def _build_remote_command(
        self, project_directory: str, compose_cmd: list[str], environment: dict[str, str] | None
    ) -> str:
        """Build remote Docker compose command with safe quoting."""
        quoted_cd = f"cd {shlex.quote(project_directory)}"
        env_prefix = ""

        if environment:
            parts = [f"{k}={shlex.quote(v)}" for k, v in environment.items()]
            env_prefix = " " + " ".join(parts)

        quoted_compose = " ".join(shlex.quote(arg) for arg in compose_cmd)
        return f"{quoted_cd} &&{env_prefix} {quoted_compose}"

    async def manage_stack(
        self, host_id: str, stack_name: str, action: str, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Unified stack lifecycle management.

        Note: Uses SSH execution instead of Docker context because Docker contexts
        cannot access compose files on remote hosts. This is a fundamental Docker
        limitation, not a bug in our code.
        See: https://github.com/docker/compose/issues/9075

        Args:
            host_id: ID of the Docker host
            stack_name: Name of the stack to manage
            action: Action to perform (up, down, restart, build, pull, logs, ps)
            options: Optional parameters for the action

        Returns:
            Operation result
        """
        # Validate inputs
        validation_error = self._validate_stack_inputs(host_id, stack_name, action)
        if validation_error:
            return validation_error

        try:
            options = options or {}

            # Get compose file information
            compose_info = await self._get_compose_file_info(host_id, stack_name)

            # Always use SSH execution for consistency with deploy_stack
            # This ensures we can access compose files on remote hosts and maintains
            # consistent behavior across all stack operations
            return await self._execute_stack_via_ssh(
                host_id, stack_name, action, options, compose_info
            )

        except (DockerCommandError, DockerContextError) as e:
            return self._build_error_response(host_id, stack_name, action, str(e))

    async def _execute_stack_via_ssh(
        self,
        host_id: str,
        stack_name: str,
        action: str,
        options: dict[str, Any],
        compose_info: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute stack command via SSH for all stack operations."""
        try:
            # Build compose arguments for the action
            try:
                compose_args = self._build_compose_args(action, options)
            except ValueError as e:
                return self._build_error_response(host_id, stack_name, action, str(e))

            # Add service filter if specified
            self._add_service_filter(compose_args, options)

            # Get Docker context for SSH connection info
            context_name = await self.context_manager.ensure_context(host_id)

            # Validate compose file exists for the action
            compose_file_validation = self._validate_compose_file_exists(
                compose_info, action, host_id, stack_name
            )
            if isinstance(compose_file_validation, dict):  # Error response
                return compose_file_validation

            compose_file_path = compose_file_validation

            # Execute via SSH with appropriate timeout
            timeout = self._determine_timeout(action)
            result = await self._execute_compose_with_file(
                context_name, stack_name, compose_file_path, compose_args, None, timeout
            )

            # Parse action-specific outputs
            output_data = self._parse_action_output(action, result)

            logger.info(
                f"Stack {action} completed via SSH",
                host_id=host_id,
                stack_name=stack_name,
                action=action,
            )

            return {
                "success": True,
                "message": f"Stack {stack_name} {action} completed successfully",
                "host_id": host_id,
                "stack_name": stack_name,
                "action": action,
                "options": options,
                "output": result,
                "data": output_data,
                "execution_method": "ssh",
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            return self._build_error_response(host_id, stack_name, action, str(e))

    def _build_compose_args(
        self, action: str, options: dict[str, Any]
    ) -> list[str]:
        """Build compose arguments based on action and options.

        Raises:
            ValueError: If the action is not supported
        """
        # Get the argument builder for the action
        builders = self._get_compose_args_builders()
        builder = builders.get(action)

        if builder:
            return builder(options)
        else:
            # Raise exception instead of returning error dict
            raise ValueError(f"Action '{action}' not supported")

    def _get_compose_args_builders(self) -> Mapping[str, Callable[..., list[str]]]:
        """Get mapping of actions to their argument builders."""
        return {
            "ps": self._build_ps_args,
            "down": self._build_down_args,
            "restart": self._build_restart_args,
            "logs": self._build_logs_args,
            "pull": self._build_pull_args,
            "build": self._build_build_args,
            "up": self._build_up_args,
        }

    def _build_ps_args(self, options: dict[str, Any]) -> list[str]:
        """Build arguments for ps action."""
        return ["ps", "--format", "json"]

    def _build_down_args(self, options: dict[str, Any]) -> list[str]:
        """Build arguments for down action."""
        args = ["down"]
        if options.get("volumes", False):
            args.append("--volumes")
        if options.get("remove_orphans", False):
            args.append("--remove-orphans")
        return args

    def _build_restart_args(self, options: dict[str, Any]) -> list[str]:
        """Build arguments for restart action."""
        args = ["restart"]
        if options.get("timeout"):
            args.extend(["--timeout", str(options["timeout"])])
        return args

    def _build_logs_args(self, options: dict[str, Any]) -> list[str]:
        """Build arguments for logs action."""
        args = ["logs"]
        if options.get("follow", False):
            args.append("--follow")
        if options.get("tail"):
            args.extend(["--tail", str(options["tail"])])
        return args

    def _build_pull_args(self, options: dict[str, Any]) -> list[str]:
        """Build arguments for pull action."""
        args = ["pull"]
        if options.get("ignore_pull_failures", False):
            args.append("--ignore-pull-failures")
        return args

    def _build_build_args(self, options: dict[str, Any]) -> list[str]:
        """Build arguments for build action."""
        args = ["build"]
        if options.get("no_cache", False):
            args.append("--no-cache")
        if options.get("pull", False):
            args.append("--pull")
        return args

    def _build_up_args(self, options: dict[str, Any]) -> list[str]:
        """Build arguments for up action."""
        args = ["up", "-d"]
        if options.get("force_recreate", False):
            args.append("--force-recreate")
        if options.get("build", False):
            args.append("--build")
        if options.get("pull", True):
            args.extend(["--pull", "always"])
        return args

    def _add_service_filter(self, compose_args: list[str], options: dict[str, Any]) -> None:
        """Add service filter to compose arguments if specified."""
        if options.get("services"):
            services = options["services"]
            if isinstance(services, list):
                compose_args.extend(services)
            else:
                compose_args.append(str(services))

    def _validate_compose_file_exists(
        self, compose_info: dict[str, Any], action: str, host_id: str, stack_name: str
    ) -> str | dict[str, Any]:
        """Validate compose file exists and return path or error."""
        if compose_info["exists"]:
            return compose_info["path"]
        else:
            if action == "up":
                return self._build_error_response(
                    host_id,
                    stack_name,
                    action,
                    f"No compose file found for stack '{stack_name}'. Use deploy_stack for new deployments.",
                )
            return self._build_error_response(
                host_id,
                stack_name,
                action,
                f"Cannot {action} stack '{stack_name}': no compose file found. Stack may not exist or was not deployed via this tool.",
            )

    def _determine_timeout(self, action: str) -> int:
        """Determine timeout based on action type."""
        return 300 if action in ["up", "build"] else 60

    def _parse_action_output(self, action: str, result: str) -> dict[str, Any] | None:
        """Parse action-specific outputs."""
        if action == "ps":
            return self._parse_ps_output_from_ssh(result)
        return None

    def _parse_ps_output_from_ssh(self, result: str) -> dict[str, Any]:
        """Parse docker compose ps output from SSH execution."""
        try:
            s = result.strip()
            # First try full JSON (array or object)
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return {"services": parsed}
            return {"services": [parsed]}
        except Exception:
            # Fallback: JSON-lines
            services = []
            for line in result.strip().split("\n"):
                try:
                    if line.strip():
                        services.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Failed to parse JSON line from compose ps", line=line)
                    continue
            return {"services": services}

    def _validate_stack_inputs(
        self, host_id: str, stack_name: str, action: str
    ) -> dict[str, Any] | None:
        """Validate stack management inputs."""
        valid_actions = ["up", "down", "restart", "build", "pull", "logs", "ps"]
        if action not in valid_actions:
            return {
                "success": False,
                "error": f"Invalid action '{action}'. Valid actions: {', '.join(valid_actions)}",
                "host_id": host_id,
                "stack_name": stack_name,
                "timestamp": datetime.now().isoformat(),
            }

        if not self._validate_stack_name(stack_name):
            return {
                "success": False,
                "error": f"Invalid stack name: {stack_name}",
                "host_id": host_id,
                "stack_name": stack_name,
                "timestamp": datetime.now().isoformat(),
            }

        return None

    async def get_stack_compose_content(self, host_id: str, stack_name: str) -> dict[str, Any]:
        """Get the docker-compose.yml content for a specific stack."""
        try:
            compose_info = await self._get_compose_file_info(host_id, stack_name)

            if not compose_info["exists"]:
                return {
                    "success": False,
                    "error": f"Compose file not found for stack '{stack_name}'",
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "timestamp": datetime.now().isoformat(),
                }

            # Get the compose file path
            compose_file_path = compose_info["path"]

            # Read the file content via SSH using centralized command builder
            host = self.config.hosts[host_id]
            ssh_cmd = build_ssh_command(host)
            ssh_cmd.append(f"cat {shlex.quote(compose_file_path)}")

            try:
                result = await asyncio.to_thread(
                    subprocess.run,  # nosec B603
                    ssh_cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired as timeout_err:
                # Extract SSH target from command for context
                ssh_target = f"{host.user}@{host.hostname}"
                logger.error(
                    "SSH cat command timeout",
                    compose_file_path=compose_file_path,
                    ssh_target=ssh_target,
                    timeout_seconds=30,
                    original_error=str(timeout_err)
                )
                return {
                    "success": False,
                    "error": f"Timeout reading compose file at {compose_file_path} from {ssh_target} (30s timeout)",
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "compose_file_path": compose_file_path,
                    "ssh_target": ssh_target,
                    "timeout_seconds": 30,
                    "timestamp": datetime.now().isoformat(),
                }

            if result.returncode == 0:
                return {
                    "success": True,
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "compose_content": result.stdout,
                    "compose_file_path": compose_file_path,
                    "timestamp": datetime.now().isoformat(),
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to read compose file: {result.stderr}",
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "timestamp": datetime.now().isoformat(),
                }

        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to get compose content: {str(e)}",
                "host_id": host_id,
                "stack_name": stack_name,
                "timestamp": datetime.now().isoformat(),
            }

    async def _get_compose_file_info(self, host_id: str, stack_name: str) -> dict[str, Any]:
        """Get compose file information for a stack."""
        compose_file_exists = await self.compose_manager.compose_file_exists(host_id, stack_name)

        if compose_file_exists:
            compose_file_path = await self.compose_manager.get_compose_file_path(
                host_id, stack_name
            )
            # Use just the filename since we cd into the project directory
            compose_filename = Path(compose_file_path).name
            return {
                "exists": True,
                "path": compose_file_path,
                "base_cmd": f"compose --project-name {stack_name} -f {compose_filename}",
            }
        else:
            return {
                "exists": False,
                "path": None,
                "base_cmd": f"compose --project-name {stack_name}",
            }

    def _build_error_response(
        self, host_id: str, stack_name: str, action: str, error: str
    ) -> dict[str, Any]:
        """Build error response."""
        logger.error(
            f"Failed to {action} stack",
            host_id=host_id,
            stack_name=stack_name,
            action=action,
            error=error,
        )
        return {
            "success": False,
            "error": error,
            "host_id": host_id,
            "stack_name": stack_name,
            "action": action,
            "timestamp": datetime.now().isoformat(),
        }

    def _validate_stack_name(self, stack_name: str) -> bool:
        """Validate stack name for security and Docker Compose compatibility."""
        import re

        # Must be alphanumeric with hyphens/underscores, no spaces
        pattern = r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"

        if not re.match(pattern, stack_name):
            return False

        # Additional length check
        if len(stack_name) > 63:  # Docker limit
            return False

        # Reserved names
        reserved = {"docker", "compose", "system", "network", "volume"}
        if stack_name.lower() in reserved:
            return False

        return True
