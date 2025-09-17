"""Docker Compose file management for persistent stack operations."""

import asyncio
import json
import os
import shlex
from pathlib import Path
from typing import Any

import docker
import structlog

from ..constants import DOCKER_COMPOSE_CONFIG_FILES, DOCKER_COMPOSE_PROJECT
from ..utils import build_ssh_command
from .config_loader import DockerMCPConfig
from .docker_context import DockerContextManager

logger = structlog.get_logger()


class ComposeManager:
    """Manages Docker Compose file locations and operations."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager

    async def get_compose_path(self, host_id: str) -> str:
        """Get the compose file path for a host, using auto-discovery if needed.

        Args:
            host_id: Host identifier

        Returns:
            Path where compose files should be stored on the host

        Raises:
            ValueError: If no compose path can be determined
        """
        host_config = self.config.hosts.get(host_id)
        if not host_config:
            raise ValueError(f"Host {host_id} not found")

        # Use explicitly configured path if available
        if host_config.compose_path:
            logger.info(
                "Using configured compose path",
                host_id=host_id,
                compose_path=host_config.compose_path,
            )
            return host_config.compose_path

        # Auto-discover compose path
        discovered_path = await self._auto_discover_compose_path(host_id)
        if discovered_path:
            logger.info(
                "Auto-discovered compose path", host_id=host_id, compose_path=discovered_path
            )
            return discovered_path

        # If no existing compose files, require configuration
        raise ValueError(
            f"No compose files found on host {host_id} and no compose_path configured. "
            "Please set compose_path in hosts.yml or deploy at least one stack first."
        )

    async def discover_compose_locations(self, host_id: str) -> dict[str, Any]:
        """Discover compose file locations by reading container labels directly.

        Returns detailed discovery information for user decision making.
        """
        try:
            discovery_result = self._create_empty_discovery_result(host_id)

            # Get all containers
            result = await self._get_containers(host_id)
            if not result:
                discovery_result["analysis"] = "No Docker containers found on this host."
                return discovery_result

            # Analyze containers for compose stacks
            location_analysis, compose_stacks = await self._analyze_containers(host_id, result)

            # Build final result
            return self._build_discovery_result(
                discovery_result, location_analysis, compose_stacks, host_id
            )

        except Exception as e:
            logger.error("Discovery failed", host_id=host_id, error=str(e))
            return self._create_error_result(host_id, str(e))

    def _create_empty_discovery_result(self, host_id: str) -> dict[str, Any]:
        """Create empty discovery result structure."""
        return {
            "host_id": host_id,
            "stacks_found": [],
            "compose_locations": {},
            "suggested_path": None,
            "analysis": "",
            "needs_configuration": True,
        }

    async def _get_containers(self, host_id: str) -> dict | None:
        """Get containers from Docker."""
        try:
            client = await self.context_manager.get_client(host_id)
            if client is None:
                return None

            # Use Docker SDK to get containers
            docker_containers = await asyncio.to_thread(client.containers.list, all=True)

            # Format output to match expected JSON lines format
            json_lines = []
            for container in docker_containers:
                container_json = {
                    "ID": container.id,
                    "Names": container.name,
                    "Image": container.image.tags[0]
                    if container.image.tags
                    else container.image.id,
                    "Command": " ".join(container.attrs.get("Config", {}).get("Cmd", []) or []),
                    "CreatedAt": container.attrs.get("Created", ""),
                    "Status": container.status,
                    "Ports": self._format_ports_from_dict(container.ports),
                    "Labels": ",".join([f"{k}={v}" for k, v in container.labels.items()]),
                }
                json_lines.append(json.dumps(container_json))

            return {"success": True, "output": "\n".join(json_lines), "returncode": 0}

        except Exception as e:
            logger.error("Failed to get containers via Docker SDK", host_id=host_id, error=str(e))
            return None

    def _format_ports_from_dict(self, ports_dict: dict[str, list[dict] | None]) -> str:
        """Format Docker SDK ports dict to match docker ps format."""
        if not ports_dict:
            return ""

        formatted_ports = []
        for container_port, host_bindings in ports_dict.items():
            if host_bindings:
                for binding in host_bindings:
                    host_ip = binding.get("HostIp", "0.0.0.0")  # noqa: S104 # Reading existing Docker port binding, not creating
                    host_port = binding.get("HostPort", "")
                    if host_ip == "0.0.0.0":  # noqa: S104 # Checking existing Docker binding value
                        formatted_ports.append(f"{host_port}:{container_port}")
                    else:
                        formatted_ports.append(f"{host_ip}:{host_port}:{container_port}")
            else:
                formatted_ports.append(container_port)

        return ", ".join(formatted_ports)

    async def _analyze_containers(self, host_id: str, result: dict) -> tuple[dict, dict]:
        """Analyze containers for compose information."""
        location_analysis = {}
        compose_stacks = {}

        for line in result["output"].strip().split("\n"):
            if not line.strip():
                continue

            try:
                container_data = json.loads(line)
                container_id = container_data.get("ID", "")[:12]

                # Get container labels
                container_info = await self._get_container_info(host_id, container_id)
                if not container_info:
                    continue

                # Process compose labels
                stack_info = self._extract_compose_info(container_info)
                if not stack_info:
                    continue

                # Update tracking
                self._update_location_analysis(stack_info, compose_stacks, location_analysis)

            except (json.JSONDecodeError, Exception) as e:
                logger.debug("Error processing container", container_id=container_id, error=str(e))
                continue

        return location_analysis, compose_stacks

    async def _get_container_info(self, host_id: str, container_id: str) -> dict | None:
        """Get detailed container information."""
        try:
            client = await self.context_manager.get_client(host_id)
            if client is None:
                return None

            # Get container using Docker SDK
            container = await asyncio.to_thread(client.containers.get, container_id)

            # Return container attributes (inspect data)
            return container.attrs

        except docker.errors.NotFound:
            logger.debug("Container not found during info retrieval", container_id=container_id)
            return None
        except docker.errors.APIError as e:
            logger.debug(
                "API error getting container info", container_id=container_id, error=str(e)
            )
            return None
        except Exception as e:
            logger.debug("Error getting container info", container_id=container_id, error=str(e))
            return None

    def _extract_compose_info(self, container_info: dict) -> dict | None:
        """Extract compose information from container labels."""
        labels = container_info.get("Config", {}).get("Labels", {}) or {}
        compose_project = labels.get(DOCKER_COMPOSE_PROJECT, "")
        compose_file = labels.get(DOCKER_COMPOSE_CONFIG_FILES, "")

        # Skip containers that aren't part of compose projects
        if not compose_project or not compose_file:
            return None

        compose_file_path = compose_file.strip()
        if not compose_file_path:
            return None

        compose_path = Path(compose_file_path)
        parent_dir = str(compose_path.parent.parent)

        return {
            "project": compose_project,
            "compose_file": compose_file_path,
            "parent_dir": parent_dir,
            "stack_dir": str(compose_path.parent),
        }

    def _update_location_analysis(
        self, stack_info: dict, compose_stacks: dict, location_analysis: dict
    ) -> None:
        """Update location analysis with stack information."""
        compose_project = stack_info["project"]
        parent_dir = stack_info["parent_dir"]

        if compose_project not in compose_stacks:
            compose_stacks[compose_project] = {
                "name": compose_project,
                "compose_file": stack_info["compose_file"],
                "parent_dir": parent_dir,
                "stack_dir": stack_info["stack_dir"],
            }

            if parent_dir not in location_analysis:
                location_analysis[parent_dir] = {"count": 0, "stacks": []}
            location_analysis[parent_dir]["count"] += 1
            location_analysis[parent_dir]["stacks"].append(compose_project)

    def _build_discovery_result(
        self, discovery_result: dict, location_analysis: dict, compose_stacks: dict, host_id: str
    ) -> dict:
        """Build the final discovery result."""
        discovery_result["stacks_found"] = list(compose_stacks.values())
        discovery_result["compose_locations"] = location_analysis

        if not location_analysis:
            discovery_result["analysis"] = (
                "No Docker Compose stacks found. Please set compose_path in hosts.yml "
                "configuration for new deployments."
            )
        elif len(location_analysis) == 1:
            self._handle_single_location(discovery_result, location_analysis)
        else:
            self._handle_multiple_locations(discovery_result, location_analysis)

        logger.info(
            "Compose location discovery completed",
            host_id=host_id,
            stacks_found=len(discovery_result["stacks_found"]),
            locations_found=len(location_analysis),
        )

        return discovery_result

    def _handle_single_location(self, discovery_result: dict, location_analysis: dict) -> None:
        """Handle case where all stacks are in one location."""
        single_location = list(location_analysis.keys())[0]
        stack_count = location_analysis[single_location]["count"]
        discovery_result["suggested_path"] = single_location
        discovery_result["analysis"] = (
            f"All {stack_count} stacks are located in subdirectories of {single_location}."
        )
        discovery_result["needs_configuration"] = False

    def _handle_multiple_locations(self, discovery_result: dict, location_analysis: dict) -> None:
        """Handle case where stacks are in multiple locations."""
        sorted_locations = sorted(
            location_analysis.items(), key=lambda x: x[1]["count"], reverse=True
        )
        primary_location, primary_data = sorted_locations[0]

        discovery_result["suggested_path"] = primary_location
        discovery_result["analysis"] = (
            f"Found stacks in {len(location_analysis)} different locations. "
            f"The majority ({primary_data['count']} stacks) are in subdirectories of "
            f"{primary_location}. Other locations: {', '.join([loc for loc, _ in sorted_locations[1:]])}"
        )

    def _create_error_result(self, host_id: str, error: str) -> dict[str, Any]:
        """Create error result."""
        return {
            "host_id": host_id,
            "stacks_found": [],
            "compose_locations": {},
            "suggested_path": None,
            "analysis": f"Discovery failed due to error: {error}. Please set compose_path in hosts.yml configuration.",
            "needs_configuration": True,
        }

    async def _auto_discover_compose_path(self, host_id: str) -> str | None:
        """Auto-discover compose file locations by analyzing existing stacks.

        Returns the directory where the majority of compose files are located.
        """
        discovery_result = await self.discover_compose_locations(host_id)
        return discovery_result.get("suggested_path")

    async def write_compose_file(self, host_id: str, stack_name: str, compose_content: str) -> str:
        """Write compose file to persistent location on remote host.

        Each stack gets its own subdirectory: {compose_path}/{stack_name}/docker-compose.yml

        Args:
            host_id: Host identifier
            stack_name: Stack name
            compose_content: Compose file content

        Returns:
            Full path to the written compose file
        """
        compose_base_dir = await self.get_compose_path(host_id)
        stack_dir = f"{compose_base_dir}/{stack_name}"
        compose_file_path = f"{stack_dir}/docker-compose.yml"

        try:
            # Create the compose file on the remote host using Docker contexts
            # We'll use a temporary container to write the file
            await self._create_compose_file_on_remote(
                host_id, stack_dir, compose_file_path, compose_content
            )

            logger.info(
                "Compose file written to remote host",
                host_id=host_id,
                stack_name=stack_name,
                stack_directory=stack_dir,
                compose_file=compose_file_path,
            )

            return compose_file_path

        except Exception as e:
            logger.error(
                "Failed to write compose file to remote host",
                host_id=host_id,
                stack_name=stack_name,
                error=str(e),
            )
            raise

    async def _create_compose_file_on_remote(
        self, host_id: str, stack_dir: str, compose_file_path: str, compose_content: str
    ) -> None:
        """Create compose file on remote host using SSH connection via Docker context."""
        import subprocess
        import tempfile

        # Get host configuration for SSH details
        host_config = self.config.hosts.get(host_id)
        if not host_config:
            raise ValueError(f"Host {host_id} not found")

        # Create a temporary file locally with the compose content
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as temp_file:
            temp_file.write(compose_content)
            temp_local_path = temp_file.name

        try:
            # Build SSH command using the helper for directory creation
            ssh_cmd_base = build_ssh_command(host_config)

            # First, create the directory on remote host
            mkdir_cmd = ssh_cmd_base + [f"mkdir -p {shlex.quote(stack_dir)}"]

            logger.debug("Creating remote directory", host_id=host_id, stack_dir=stack_dir)

            mkdir_result = await asyncio.to_thread(
                subprocess.run,  # nosec B603
                mkdir_cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if mkdir_result.returncode != 0:
                raise Exception(f"Failed to create directory on remote host: {mkdir_result.stderr}")

            # Then, copy the file using scp
            scp_cmd = ["scp", "-B"]

            # Add port if not default
            if host_config.port != 22:
                scp_cmd.extend(["-P", str(host_config.port)])

            # Add identity file if specified
            if host_config.identity_file:
                scp_cmd.extend(["-i", host_config.identity_file])

            # Add common SCP options for automation
            scp_cmd.extend(
                [
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    "-o",
                    "LogLevel=ERROR",
                ]
            )

            # Add source and destination
            ssh_host = f"{host_config.user}@{host_config.hostname}"
            scp_cmd.extend([temp_local_path, f"{ssh_host}:{compose_file_path}"])

            logger.debug(
                "Copying compose file to remote host",
                host_id=host_id,
                compose_file=compose_file_path,
            )

            scp_result = await asyncio.to_thread(
                subprocess.run,  # nosec B603 - SCP command execution is intentional
                scp_cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if scp_result.returncode != 0:
                raise Exception(f"Failed to copy compose file to remote host: {scp_result.stderr}")

            logger.info(
                "Successfully created compose file on remote host",
                host_id=host_id,
                compose_file=compose_file_path,
            )

        except Exception as e:
            logger.error(
                "Failed to create compose file via SSH",
                host_id=host_id,
                compose_file=compose_file_path,
                error=str(e),
            )
            raise Exception(f"Could not create compose file on remote host: {e}") from e
        finally:
            # Clean up temporary file
            try:
                os.unlink(temp_local_path)
            except Exception as e:
                logger.debug(
                    "Failed to cleanup temporary file", temp_path=temp_local_path, error=str(e)
                )

    async def _file_exists_via_ssh(self, host_id: str, file_path: str) -> bool:
        """Check if a specific file exists on remote host via SSH.

        Args:
            host_id: Host identifier
            file_path: Full path to file to check

        Returns:
            True if file exists on remote host
        """
        try:
            import asyncio
            import subprocess

            # Get host configuration for SSH details
            host_config = self.config.hosts.get(host_id)
            if not host_config:
                return False

            # Build SSH command using the helper and append test command
            ssh_cmd = build_ssh_command(host_config)
            ssh_cmd.append(f"test -f {shlex.quote(file_path)}")

            # Execute the command
            result = await asyncio.to_thread(
                subprocess.run,  # nosec B603
                ssh_cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

            # Return code 0 means file exists, non-zero means it doesn't
            return result.returncode == 0

        except Exception as e:
            logger.debug(
                "Error checking file existence via SSH",
                host_id=host_id,
                file_path=file_path,
                error=str(e),
            )
            return False

    async def get_compose_file_path(self, host_id: str, stack_name: str) -> str:
        """Get the actual path for a stack's compose file, checking multiple extensions.

        Checks for compose files in order of preference:
        1. docker-compose.yml
        2. docker-compose.yaml
        3. compose.yml
        4. compose.yaml

        Args:
            host_id: Host identifier
            stack_name: Stack name

        Returns:
            Path to existing compose file, or default path if none exist
        """
        compose_base_dir = await self.get_compose_path(host_id)

        # Check for common compose file names in order of preference
        possible_files = [
            f"{compose_base_dir}/{stack_name}/docker-compose.yml",
            f"{compose_base_dir}/{stack_name}/docker-compose.yaml",
            f"{compose_base_dir}/{stack_name}/compose.yml",
            f"{compose_base_dir}/{stack_name}/compose.yaml",
        ]

        # Check each possible file path
        for file_path in possible_files:
            if await self._file_exists_via_ssh(host_id, file_path):
                logger.debug(
                    "Found compose file with extension check",
                    host_id=host_id,
                    stack_name=stack_name,
                    file_path=file_path,
                )
                return file_path

        # Default to .yml if none exist (for new deployments)
        default_path = f"{compose_base_dir}/{stack_name}/docker-compose.yml"
        logger.debug(
            "No existing compose file found, using default",
            host_id=host_id,
            stack_name=stack_name,
            default_path=default_path,
        )
        return default_path

    async def compose_file_exists(self, host_id: str, stack_name: str) -> bool:
        """Check if a compose file exists for a stack.

        Args:
            host_id: Host identifier
            stack_name: Stack name

        Returns:
            True if compose file exists
        """
        try:
            import asyncio
            import subprocess

            # Get host configuration for SSH details
            host_config = self.config.hosts.get(host_id)
            if not host_config:
                return False

            compose_file_path = await self.get_compose_file_path(host_id, stack_name)

            # Build SSH command using the helper and append test command
            ssh_cmd = build_ssh_command(host_config)
            ssh_cmd.append(f"test -f {shlex.quote(compose_file_path)}")

            # Execute the command
            result = await asyncio.to_thread(
                subprocess.run,  # nosec B603
                ssh_cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

            # Return code 0 means file exists, non-zero means it doesn't
            return result.returncode == 0

        except Exception as e:
            logger.debug(
                "Error checking compose file existence",
                host_id=host_id,
                stack_name=stack_name,
                error=str(e),
            )
            return False
