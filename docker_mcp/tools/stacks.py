"""Stack deployment MCP tools."""

import asyncio
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from ..core.compose_manager import ComposeManager
from ..core.config import DockerMCPConfig
from ..core.docker_context import DockerContextManager
from ..core.exceptions import DockerCommandError, DockerContextError
from ..models.container import StackInfo

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
            # Get list of compose projects
            cmd = "compose ls --format json"
            result = await self.context_manager.execute_docker_command(host_id, cmd)

            stacks = []
            if isinstance(result, dict) and "output" in result:
                try:
                    compose_data = json.loads(result["output"])
                    if isinstance(compose_data, list):
                        for stack_data in compose_data:
                            stack = StackInfo(
                                name=stack_data.get("Name", ""),
                                host_id=host_id,
                                services=stack_data.get("Service", "").split(",")
                                if stack_data.get("Service")
                                else [],
                                status=stack_data.get("Status", ""),
                                created=stack_data.get("CreatedAt", ""),
                                updated=stack_data.get("UpdatedAt", ""),
                            )
                            stacks.append(stack.model_dump())
                except json.JSONDecodeError:
                    logger.warning("Failed to parse compose ls output", host_id=host_id)

            logger.info("Listed stacks", host_id=host_id, count=len(stacks))
            return {
                "success": True,
                "stacks": stacks,
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
    ) -> str:
        """Execute docker compose command via SSH on remote host."""
        # Extract directory from full path
        compose_path = Path(compose_file_path)
        project_directory = str(compose_path.parent)

        # Build docker compose command to run on remote host
        compose_cmd = [
            "docker",
            "compose",
            "--project-name",
            project_name,
            "-f",
            compose_file_path,
        ]
        compose_cmd.extend(compose_args)

        # Get host config for SSH connection
        host_id = self._extract_host_id_from_context(context_name)
        host_config = self.config.hosts.get(host_id)
        if not host_config:
            raise DockerCommandError(f"Host {host_id} not found in configuration")

        # Build SSH command
        ssh_cmd = self._build_ssh_command(host_config)

        # Build remote command
        remote_cmd = self._build_remote_command(project_directory, compose_cmd, environment)

        ssh_cmd.extend([f"{host_config.user}@{host_config.hostname}", remote_cmd])

        # Debug logging
        logger.debug(
            "Executing SSH command",
            host_id=host_id,
            ssh_command=" ".join(ssh_cmd),
            remote_command=remote_cmd,
        )

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    ssh_cmd,
                    check=False,
                    text=True,
                    capture_output=True,
                    timeout=300,  # 5 minute timeout for deployment
                ),
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip()
                raise DockerCommandError(f"Docker compose command failed: {error_msg}")

            return result.stdout.strip()

        except subprocess.TimeoutExpired as e:
            raise DockerCommandError(f"Docker compose command timed out: {e}") from e
        except Exception as e:
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

    def _build_ssh_command(self, host_config) -> list[str]:
        """Build base SSH command with options."""
        ssh_cmd = ["ssh"]

        # Add port if not default
        if host_config.port != 22:
            ssh_cmd.extend(["-p", str(host_config.port)])

        # Add identity file if specified
        if host_config.identity_file:
            ssh_cmd.extend(["-i", host_config.identity_file])

        # Add common SSH options
        ssh_cmd.extend([
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
        ])

        return ssh_cmd

    def _build_remote_command(
        self, project_directory: str, compose_cmd: list[str], environment: dict[str, str] | None
    ) -> str:
        """Build remote Docker compose command."""
        # Build environment variables for remote execution
        env_vars = []
        if environment:
            for key, value in environment.items():
                env_vars.append(f"{key}={value}")

        # Combine environment and docker compose command
        if env_vars:
            return f"cd {project_directory} && {' '.join(env_vars)} {' '.join(compose_cmd)}"
        else:
            return f"cd {project_directory} && {' '.join(compose_cmd)}"

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
            return await self._execute_stack_via_ssh(host_id, stack_name, action, options, compose_info)

        except (DockerCommandError, DockerContextError) as e:
            return self._build_error_response(host_id, stack_name, action, str(e))

    async def _execute_stack_via_ssh(
        self, host_id: str, stack_name: str, action: str, options: dict[str, Any], compose_info: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute stack command via SSH for all stack operations."""
        try:
            # Build compose arguments for the action
            compose_args = []
            
            if action == "ps":
                compose_args = ["ps", "--format", "json"]
            elif action == "down":
                compose_args = ["down"]
                if options.get("volumes", False):
                    compose_args.append("--volumes")
                if options.get("remove_orphans", False):
                    compose_args.append("--remove-orphans")
            elif action == "restart":
                compose_args = ["restart"]
                if options.get("timeout"):
                    compose_args.extend(["--timeout", str(options["timeout"])])
            elif action == "logs":
                compose_args = ["logs"]
                if options.get("follow", False):
                    compose_args.append("--follow")
                if options.get("tail"):
                    compose_args.extend(["--tail", str(options["tail"])])
            elif action == "pull":
                compose_args = ["pull"]
                if options.get("ignore_pull_failures", False):
                    compose_args.append("--ignore-pull-failures")
            elif action == "build":
                compose_args = ["build"]
                if options.get("no_cache", False):
                    compose_args.append("--no-cache")
                if options.get("pull", False):
                    compose_args.append("--pull")
            elif action == "up":
                compose_args = ["up", "-d"]
                if options.get("force_recreate", False):
                    compose_args.append("--force-recreate")
                if options.get("build", False):
                    compose_args.append("--build")
                if options.get("pull", True):
                    compose_args.extend(["--pull", "always"])
            else:
                return self._build_error_response(host_id, stack_name, action, f"Action '{action}' not supported")

            # Add service filter if specified
            if options.get("services"):
                services = options["services"]
                if isinstance(services, list):
                    compose_args.extend(services)
                else:
                    compose_args.append(str(services))

            # Get Docker context for SSH connection info
            context_name = await self.context_manager.ensure_context(host_id)
            
            # For stacks with compose files, use the full path
            # For project-only stacks, execute in a default directory
            if compose_info["exists"]:
                compose_file_path = compose_info["path"]
            else:
                # For project-only stacks, we need to determine where to execute
                # This is for cases where stack was created without deploy_stack
                # Default to a standard location or fail gracefully
                if action == "up":
                    return self._build_error_response(
                        host_id, stack_name, action, 
                        f"No compose file found for stack '{stack_name}'. Use deploy_stack for new deployments."
                    )
                # For other actions on project-only stacks, we can't proceed without a compose file
                return self._build_error_response(
                    host_id, stack_name, action,
                    f"Cannot {action} stack '{stack_name}': no compose file found. Stack may not exist or was not deployed via this tool."
                )
            
            # Execute via SSH using the same method as deploy_stack
            result = await self._execute_compose_with_file(
                context_name, stack_name, compose_file_path, compose_args, None
            )

            # Parse action-specific outputs
            output_data = None
            if action == "ps":
                output_data = self._parse_ps_output_from_ssh(result)

            logger.info(
                f"Stack {action} completed via SSH", 
                host_id=host_id, 
                stack_name=stack_name, 
                action=action
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

    def _parse_ps_output_from_ssh(self, result: str) -> dict[str, Any]:
        """Parse docker compose ps output from SSH execution."""
        try:
            services = []
            # SSH result is just the raw output string from docker compose ps --format json
            # Each line should be a JSON object representing a service
            for line in result.strip().split('\n'):
                if line.strip():
                    try:
                        service_data = json.loads(line)
                        services.append(service_data)
                    except json.JSONDecodeError:
                        continue
            return {"services": services}
        except Exception:
            return {"services": []}

    def _validate_stack_inputs(self, host_id: str, stack_name: str, action: str) -> dict[str, Any] | None:
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

    async def _get_compose_file_info(self, host_id: str, stack_name: str) -> dict[str, Any]:
        """Get compose file information for a stack."""
        compose_file_exists = await self.compose_manager.compose_file_exists(host_id, stack_name)

        if compose_file_exists:
            compose_file_path = await self.compose_manager.get_compose_file_path(host_id, stack_name)
            return {
                "exists": True,
                "path": compose_file_path,
                "base_cmd": f"compose --project-name {stack_name} -f {compose_file_path}"
            }
        else:
            return {
                "exists": False,
                "path": None,
                "base_cmd": f"compose --project-name {stack_name}"
            }

    async def _build_stack_command(
        self, action: str, stack_name: str, options: dict[str, Any], compose_info: dict[str, Any]
    ) -> dict[str, Any]:
        """Build Docker Compose command for specific action."""
        cmd = compose_info["base_cmd"]

        # Special validation for 'up' action
        if action == "up" and not compose_info["exists"]:
            return {
                "success": False,
                "error": f"No compose file found for stack '{stack_name}'. Use deploy_stack tool for new deployments.",
                "timestamp": datetime.now().isoformat(),
            }

        # Build action-specific commands
        if action == "up":
            cmd += self._build_up_command(options)
        elif action == "down":
            cmd += self._build_down_command(options)
        elif action == "restart":
            cmd += self._build_restart_command(options)
        elif action == "build":
            cmd += self._build_build_command(options)
        elif action == "pull":
            cmd += self._build_pull_command(options)
        elif action == "logs":
            cmd += self._build_logs_command(options)
        elif action == "ps":
            cmd += " ps --format json"

        # Add service filter if specified
        if options.get("services"):
            cmd += self._build_services_filter(options["services"])

        return {"success": True, "command": cmd}

    def _build_up_command(self, options: dict[str, Any]) -> str:
        """Build 'up' command options."""
        cmd = " up -d"
        if options.get("force_recreate", False):
            cmd += " --force-recreate"
        if options.get("build", False):
            cmd += " --build"
        if options.get("pull", True):
            cmd += " --pull always"
        return cmd

    def _build_down_command(self, options: dict[str, Any]) -> str:
        """Build 'down' command options."""
        cmd = " down"
        if options.get("volumes", False):
            cmd += " --volumes"
        if options.get("remove_orphans", False):
            cmd += " --remove-orphans"
        return cmd

    def _build_restart_command(self, options: dict[str, Any]) -> str:
        """Build 'restart' command options."""
        cmd = " restart"
        if options.get("timeout"):
            cmd += f" --timeout {options['timeout']}"
        return cmd

    def _build_build_command(self, options: dict[str, Any]) -> str:
        """Build 'build' command options."""
        cmd = " build"
        if options.get("no_cache", False):
            cmd += " --no-cache"
        if options.get("pull", False):
            cmd += " --pull"
        return cmd

    def _build_pull_command(self, options: dict[str, Any]) -> str:
        """Build 'pull' command options."""
        cmd = " pull"
        if options.get("ignore_pull_failures", False):
            cmd += " --ignore-pull-failures"
        return cmd

    def _build_logs_command(self, options: dict[str, Any]) -> str:
        """Build 'logs' command options."""
        cmd = " logs"
        if options.get("follow", False):
            cmd += " --follow"
        if options.get("tail"):
            cmd += f" --tail {options['tail']}"
        return cmd

    def _build_services_filter(self, services: Any) -> str:
        """Build services filter for command."""
        if isinstance(services, list):
            return " " + " ".join(services)
        else:
            return f" {services}"

    def _parse_stack_output(self, action: str, result: Any) -> dict[str, Any] | None:
        """Parse specific command outputs."""
        if action == "ps" and isinstance(result, dict) and "output" in result:
            try:
                services = []
                for line in result["output"].strip().split("\\n"):
                    if line.strip():
                        try:
                            service_data = json.loads(line)
                            services.append(service_data)
                        except json.JSONDecodeError:
                            continue
                return {"services": services}
            except Exception:
                return {"services": []}
        return None

    def _build_success_response(
        self, host_id: str, stack_name: str, action: str, options: dict[str, Any], result: Any, output_data: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Build success response."""
        return {
            "success": True,
            "message": f"Stack {stack_name} {action} completed successfully",
            "host_id": host_id,
            "stack_name": stack_name,
            "action": action,
            "options": options,
            "output": result.get("output", "") if isinstance(result, dict) else str(result),
            "data": output_data,
            "timestamp": datetime.now().isoformat(),
        }

    def _build_error_response(self, host_id: str, stack_name: str, action: str, error: str) -> dict[str, Any]:
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
