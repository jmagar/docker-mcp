"""Container management MCP tools."""

import json
from datetime import datetime
from typing import Any

import structlog

from ..core.config import DockerMCPConfig
from ..core.docker_context import DockerContextManager
from ..core.exceptions import DockerCommandError, DockerContextError
from ..models.container import (
    ContainerStats,
    PortConflict,
    PortMapping,
)

logger = structlog.get_logger()


class ContainerTools:
    """Container management tools for MCP."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager

    async def list_containers(
        self, host_id: str, all_containers: bool = False, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        """List containers on a Docker host with pagination and enhanced information.

        Args:
            host_id: ID of the Docker host
            all_containers: Include stopped containers (default: False)
            limit: Maximum number of containers to return (default: 20)
            offset: Number of containers to skip (default: 0)

        Returns:
            Dictionary with paginated container information including volumes, networks, and compose info
        """
        try:
            # Build Docker command
            cmd = "ps --format json --no-trunc"
            if all_containers:
                cmd += " --all"

            result = await self.context_manager.execute_docker_command(host_id, cmd)

            # Parse container data
            containers = []
            if isinstance(result, dict) and "output" in result:
                # Parse JSON lines output
                for line in result["output"].strip().split("\n"):
                    if line.strip():
                        try:
                            container_data = json.loads(line)

                            # Get enhanced container info including inspect data
                            container_id = container_data.get("ID", "")[:12]
                            inspect_info = await self._get_container_inspect_info(
                                host_id, container_id
                            )

                            # Return enhanced container info
                            container_summary = {
                                "id": container_id,
                                "name": container_data.get("Names", "").lstrip("/"),
                                "image": container_data.get("Image", ""),
                                "status": container_data.get("Status", ""),
                                "state": container_data.get("State", ""),
                                "ports": self._parse_ports_summary(container_data.get("Ports", "")),
                                "host_id": host_id,
                                "volumes": inspect_info.get("volumes", []),
                                "networks": inspect_info.get("networks", []),
                                "compose_project": inspect_info.get("compose_project", ""),
                                "compose_file": inspect_info.get("compose_file", ""),
                            }
                            containers.append(container_summary)
                        except json.JSONDecodeError:
                            logger.warning("Failed to parse container JSON", line=line)

            # Apply pagination
            total_count = len(containers)
            paginated_containers = containers[offset : offset + limit]

            logger.info(
                "Listed containers",
                host_id=host_id,
                total=total_count,
                returned=len(paginated_containers),
                offset=offset,
                limit=limit,
            )

            return {
                "containers": paginated_containers,
                "pagination": {
                    "total": total_count,
                    "limit": limit,
                    "offset": offset,
                    "returned": len(paginated_containers),
                    "has_next": (offset + limit) < total_count,
                    "has_prev": offset > 0,
                },
            }

        except (DockerCommandError, DockerContextError) as e:
            logger.error("Failed to list containers", host_id=host_id, error=str(e))
            raise

    async def get_container_info(self, host_id: str, container_id: str) -> dict[str, Any]:
        """Get detailed information about a specific container.

        Args:
            host_id: ID of the Docker host
            container_id: Container ID or name

        Returns:
            Detailed container information
        """
        try:
            cmd = f"inspect {container_id}"
            result = await self.context_manager.execute_docker_command(host_id, cmd)

            # The execute_docker_command should return parsed JSON for inspect commands
            if isinstance(result, list) and len(result) > 0:
                container_data = result[0]
            elif isinstance(result, dict) and "output" in result:
                try:
                    inspect_data = json.loads(result["output"])
                    if isinstance(inspect_data, list) and len(inspect_data) > 0:
                        container_data = inspect_data[0]
                    else:
                        logger.error("Unexpected inspect data format", data=inspect_data)
                        return {"error": "Unexpected container data format"}
                except json.JSONDecodeError as e:
                    logger.error(
                        "Failed to parse container inspect JSON",
                        host_id=host_id,
                        container_id=container_id,
                        error=str(e),
                        raw_output=result.get("output", "")[:500],
                    )
                    return {"error": f"Failed to parse container data: {e}"}
            else:
                logger.error("No container data in result", result=result)
                return {"error": "No container data received"}

            # Extract detailed information from inspect data
            mounts = container_data.get("Mounts", [])
            network_settings = container_data.get("NetworkSettings", {})
            labels = container_data.get("Config", {}).get("Labels", {}) or {}

            # Parse volume mounts
            volumes = []
            for mount in mounts:
                if mount.get("Type") == "bind":
                    volumes.append(f"{mount.get('Source', '')}:{mount.get('Destination', '')}")
                elif mount.get("Type") == "volume":
                    volumes.append(f"{mount.get('Name', '')}:{mount.get('Destination', '')}")

            # Parse networks
            networks = list(network_settings.get("Networks", {}).keys())

            # Extract compose information from labels
            compose_project = labels.get("com.docker.compose.project", "")
            compose_file = labels.get("com.docker.compose.project.config_files", "")

            container_info = {
                "container_id": container_data.get("Id", ""),
                "name": container_data.get("Name", "").lstrip("/"),
                "image": container_data.get("Config", {}).get("Image", ""),
                "status": container_data.get("State", {}).get("Status", ""),
                "state": container_data.get("State", {}),
                "created": container_data.get("Created", ""),
                "ports": network_settings.get("Ports", {}),
                "labels": labels,
                "volumes": volumes,
                "networks": networks,
                "compose_project": compose_project,
                "compose_file": compose_file,
                "host_id": host_id,
                "config": container_data.get("Config", {}),
                "network_settings": network_settings,
                "mounts": mounts,
            }

            logger.info("Retrieved container info", host_id=host_id, container_id=container_id)
            return container_info

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to get container info",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {"error": str(e)}

    async def start_container(self, host_id: str, container_id: str) -> dict[str, Any]:
        """Start a container on a Docker host.

        Args:
            host_id: ID of the Docker host
            container_id: Container ID or name to start

        Returns:
            Operation result
        """
        try:
            cmd = f"start {container_id}"
            await self.context_manager.execute_docker_command(host_id, cmd)

            logger.info("Container started", host_id=host_id, container_id=container_id)
            return {
                "success": True,
                "message": f"Container {container_id} started successfully",
                "container_id": container_id,
                "host_id": host_id,
                "timestamp": datetime.now().isoformat(),
            }

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to start container",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {
                "success": False,
                "message": f"Failed to start container {container_id}: {str(e)}",
                "container_id": container_id,
                "host_id": host_id,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    async def stop_container(
        self, host_id: str, container_id: str, timeout: int = 10
    ) -> dict[str, Any]:
        """Stop a container on a Docker host.

        Args:
            host_id: ID of the Docker host
            container_id: Container ID or name to stop
            timeout: Timeout in seconds before force killing

        Returns:
            Operation result
        """
        try:
            cmd = f"stop --time {timeout} {container_id}"
            await self.context_manager.execute_docker_command(host_id, cmd)

            logger.info(
                "Container stopped", host_id=host_id, container_id=container_id, timeout=timeout
            )
            return {
                "success": True,
                "message": f"Container {container_id} stopped successfully",
                "container_id": container_id,
                "host_id": host_id,
                "timeout": timeout,
                "timestamp": datetime.now().isoformat(),
            }

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to stop container", host_id=host_id, container_id=container_id, error=str(e)
            )
            return {
                "success": False,
                "message": f"Failed to stop container {container_id}: {str(e)}",
                "container_id": container_id,
                "host_id": host_id,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    async def restart_container(
        self, host_id: str, container_id: str, timeout: int = 10
    ) -> dict[str, Any]:
        """Restart a container on a Docker host.

        Args:
            host_id: ID of the Docker host
            container_id: Container ID or name to restart
            timeout: Timeout in seconds before force killing

        Returns:
            Operation result
        """
        try:
            cmd = f"restart --time {timeout} {container_id}"
            await self.context_manager.execute_docker_command(host_id, cmd)

            logger.info(
                "Container restarted", host_id=host_id, container_id=container_id, timeout=timeout
            )
            return {
                "success": True,
                "message": f"Container {container_id} restarted successfully",
                "container_id": container_id,
                "host_id": host_id,
                "timeout": timeout,
                "timestamp": datetime.now().isoformat(),
            }

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to restart container",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {
                "success": False,
                "message": f"Failed to restart container {container_id}: {str(e)}",
                "container_id": container_id,
                "host_id": host_id,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    async def get_container_stats(self, host_id: str, container_id: str) -> dict[str, Any]:
        """Get resource statistics for a container.

        Args:
            host_id: ID of the Docker host
            container_id: Container ID or name

        Returns:
            Container resource statistics
        """
        try:
            cmd = f"stats --no-stream --format json {container_id}"
            result = await self.context_manager.execute_docker_command(host_id, cmd)

            if isinstance(result, dict) and "output" in result:
                try:
                    stats_data = json.loads(result["output"])

                    # Parse stats data
                    stats = ContainerStats(
                        container_id=container_id,
                        host_id=host_id,
                        cpu_percentage=self._parse_percentage(stats_data.get("CPUPerc", "0%")),
                        memory_usage=self._parse_memory(stats_data.get("MemUsage", "0B / 0B"))[0],
                        memory_limit=self._parse_memory(stats_data.get("MemUsage", "0B / 0B"))[1],
                        memory_percentage=self._parse_percentage(stats_data.get("MemPerc", "0%")),
                        network_rx=self._parse_network(stats_data.get("NetIO", "0B / 0B"))[0],
                        network_tx=self._parse_network(stats_data.get("NetIO", "0B / 0B"))[1],
                        block_read=self._parse_block_io(stats_data.get("BlockIO", "0B / 0B"))[0],
                        block_write=self._parse_block_io(stats_data.get("BlockIO", "0B / 0B"))[1],
                        pids=int(stats_data.get("PIDs", 0)),
                    )

                    logger.debug(
                        "Retrieved container stats", host_id=host_id, container_id=container_id
                    )
                    return stats.model_dump()

                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(
                        "Failed to parse stats data",
                        host_id=host_id,
                        container_id=container_id,
                        error=str(e),
                    )
                    return {"error": f"Failed to parse stats data: {e}"}

            return {"error": "No stats data received"}

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to get container stats",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {"error": str(e)}

    def _parse_ports(self, ports_str: str) -> list[dict[str, Any]]:
        """Parse Docker ports string."""
        if not ports_str:
            return []

        ports = []
        for port_mapping in ports_str.split(", "):
            if "->" in port_mapping:
                host_part, container_part = port_mapping.split("->")
                ports.append({"host": host_part.strip(), "container": container_part.strip()})

        return ports

    def _parse_ports_summary(self, ports_str: str) -> list[str]:
        """Parse Docker ports string into simplified format."""
        if not ports_str:
            return []

        ports = []
        for port_mapping in ports_str.split(", "):
            if "->" in port_mapping:
                host_part, container_part = port_mapping.split("->")
                ports.append(f"{host_part.strip()}→{container_part.strip()}")

        return ports

    def _parse_labels(self, labels_data: Any) -> dict[str, str]:
        """Parse Docker labels that can be a dict or comma-separated string."""
        if isinstance(labels_data, dict):
            return labels_data
        elif isinstance(labels_data, str):
            labels = {}
            if labels_data:
                # Parse comma-separated key=value pairs
                for item in labels_data.split(","):
                    if "=" in item:
                        key, value = item.split("=", 1)
                        labels[key.strip()] = value.strip()
            return labels
        else:
            return {}

    def _parse_percentage(self, perc_str: str) -> float | None:
        """Parse percentage string like '50.5%'."""
        try:
            return float(perc_str.rstrip("%"))
        except (ValueError, AttributeError):
            return None

    def _parse_memory(self, mem_str: str) -> tuple[int | None, int | None]:
        """Parse memory string like '1.5GB / 4GB'."""
        try:
            parts = mem_str.split(" / ")
            if len(parts) == 2:
                usage = self._parse_size(parts[0].strip())
                limit = self._parse_size(parts[1].strip())
                return usage, limit
        except (ValueError, AttributeError):
            pass
        return None, None

    def _parse_network(self, net_str: str) -> tuple[int | None, int | None]:
        """Parse network I/O string like '1.2kB / 800B'."""
        return self._parse_memory(net_str)  # Same format

    def _parse_block_io(self, block_str: str) -> tuple[int | None, int | None]:
        """Parse block I/O string like '1.2MB / 800kB'."""
        return self._parse_memory(block_str)  # Same format

    def _parse_size(self, size_str: str) -> int | None:
        """Parse size string like '1.5GB' to bytes."""
        try:
            size_str = size_str.strip()
            if size_str == "0":
                return 0

            units = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}

            for unit, multiplier in units.items():
                if size_str.endswith(unit):
                    value = float(size_str[: -len(unit)])
                    return int(value * multiplier)

            # If no unit, assume bytes
            return int(float(size_str))

        except (ValueError, AttributeError):
            return None

    async def _get_container_inspect_info(self, host_id: str, container_id: str) -> dict[str, Any]:
        """Get basic container inspect info for enhanced listings."""
        try:
            cmd = f"inspect {container_id}"
            result = await self.context_manager.execute_docker_command(host_id, cmd)

            container_data = None
            if isinstance(result, list) and len(result) > 0:
                container_data = result[0]
            elif isinstance(result, dict) and "output" in result:
                try:
                    inspect_data = json.loads(result["output"])
                    if isinstance(inspect_data, list) and len(inspect_data) > 0:
                        container_data = inspect_data[0]
                except json.JSONDecodeError:
                    return {
                        "volumes": [],
                        "networks": [],
                        "compose_project": "",
                        "compose_file": "",
                    }

            if not container_data:
                return {"volumes": [], "networks": [], "compose_project": "", "compose_file": ""}

            # Extract basic info
            mounts = container_data.get("Mounts", [])
            network_settings = container_data.get("NetworkSettings", {})
            labels = container_data.get("Config", {}).get("Labels", {}) or {}

            # Parse volume mounts
            volumes = []
            for mount in mounts:
                if mount.get("Type") == "bind":
                    volumes.append(f"{mount.get('Source', '')}:{mount.get('Destination', '')}")
                elif mount.get("Type") == "volume":
                    volumes.append(f"{mount.get('Name', '')}:{mount.get('Destination', '')}")

            # Parse networks
            networks = list(network_settings.get("Networks", {}).keys())

            # Extract compose information
            compose_project = labels.get("com.docker.compose.project", "")
            compose_file = labels.get("com.docker.compose.project.config_files", "")

            return {
                "volumes": volumes,
                "networks": networks,
                "compose_project": compose_project,
                "compose_file": compose_file,
            }

        except Exception:
            # Don't log errors for this helper function, just return empty data
            return {"volumes": [], "networks": [], "compose_project": "", "compose_file": ""}

    async def manage_container(
        self, host_id: str, container_id: str, action: str, force: bool = False, timeout: int = 10
    ) -> dict[str, Any]:
        """Unified container action management.

        Args:
            host_id: ID of the Docker host
            container_id: Container ID or name
            action: Action to perform (start, stop, restart, pause, unpause, remove)
            force: Force the action (for remove/stop)
            timeout: Timeout for stop/restart operations

        Returns:
            Operation result
        """
        valid_actions = ["start", "stop", "restart", "pause", "unpause", "remove"]
        if action not in valid_actions:
            return {
                "success": False,
                "error": f"Invalid action '{action}'. Valid actions: {', '.join(valid_actions)}",
                "container_id": container_id,
                "host_id": host_id,
                "timestamp": datetime.now().isoformat(),
            }

        try:
            # Build command based on action
            cmd = self._build_container_command(action, container_id, force, timeout)

            await self.context_manager.execute_docker_command(host_id, cmd)

            logger.info(
                f"Container {action} completed",
                host_id=host_id,
                container_id=container_id,
                action=action,
                force=force,
            )

            return {
                "success": True,
                "message": f"Container {container_id} {action}{'d' if action.endswith('e') else 'ed'} successfully",
                "container_id": container_id,
                "host_id": host_id,
                "action": action,
                "force": force,
                "timeout": timeout if action in ["stop", "restart"] else None,
                "timestamp": datetime.now().isoformat(),
            }

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                f"Failed to {action} container",
                host_id=host_id,
                container_id=container_id,
                action=action,
                error=str(e),
            )
            return {
                "success": False,
                "message": f"Failed to {action} container {container_id}: {str(e)}",
                "container_id": container_id,
                "host_id": host_id,
                "action": action,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    def _build_container_command(self, action: str, container_id: str, force: bool, timeout: int) -> str:
        """Build Docker command based on action."""
        if action == "start":
            return f"start {container_id}"
        elif action == "stop":
            if force:
                return f"kill {container_id}"
            return f"stop --time {timeout} {container_id}"
        elif action == "restart":
            return f"restart --time {timeout} {container_id}"
        elif action == "pause":
            return f"pause {container_id}"
        elif action == "unpause":
            return f"unpause {container_id}"
        elif action == "remove":
            if force:
                return f"rm -f {container_id}"
            return f"rm {container_id}"
        else:
            raise ValueError(f"Unknown action: {action}")

    async def list_host_ports(self, host_id: str, include_stopped: bool = False) -> dict[str, Any]:
        """List all ports currently in use by containers on a Docker host.

        Args:
            host_id: ID of the Docker host
            include_stopped: Include ports from stopped containers (default: False)

        Returns:
            Comprehensive port usage information with conflict detection
        """
        try:
            # Get container data
            containers = await self._get_containers_for_port_analysis(host_id, include_stopped)
            total_containers = len(containers)

            # Collect all port mappings from containers
            port_mappings = await self._collect_port_mappings(host_id, containers)

            # Detect and mark port conflicts
            conflicts = self._detect_port_conflicts(port_mappings)

            # Generate summary statistics
            summary = self._generate_port_summary(port_mappings, conflicts)

            logger.info(
                "Listed host ports",
                host_id=host_id,
                total_ports=len(port_mappings),
                total_containers=total_containers,
                conflicts=len(conflicts),
            )

            return {
                "success": True,
                "host_id": host_id,
                "total_ports": len(port_mappings),
                "total_containers": total_containers,
                "port_mappings": [mapping.model_dump() for mapping in port_mappings],
                "conflicts": [conflict.model_dump() for conflict in conflicts],
                "summary": summary,
                "timestamp": datetime.now().isoformat(),
            }

        except (DockerCommandError, DockerContextError) as e:
            logger.error("Failed to list host ports", host_id=host_id, error=str(e))
            raise
        except Exception as e:
            logger.error("Unexpected error listing host ports", host_id=host_id, error=str(e))
            raise DockerCommandError(f"Failed to list ports: {e}") from e

    async def _get_containers_for_port_analysis(self, host_id: str, include_stopped: bool) -> list[dict[str, Any]]:
        """Get container data for port analysis."""
        cmd = "ps --format json --no-trunc"
        if include_stopped:
            cmd += " --all"

        result = await self.context_manager.execute_docker_command(host_id, cmd)
        containers = []

        if isinstance(result, dict) and "output" in result:
            for line in result["output"].strip().split("\n"):
                if line.strip():
                    try:
                        container_data = json.loads(line)
                        containers.append(container_data)
                    except json.JSONDecodeError:
                        logger.warning("Failed to parse container JSON", line=line)

        return containers

    async def _collect_port_mappings(self, host_id: str, containers: list[dict[str, Any]]) -> list[PortMapping]:
        """Collect port mappings from all containers."""
        port_mappings = []

        for container_data in containers:
            container_id = container_data.get("ID", "")[:12]
            container_name = container_data.get("Names", "").lstrip("/")
            image = container_data.get("Image", "")

            # Get port mappings for this container
            container_mappings = await self._get_container_port_mappings(
                host_id, container_id, container_name, image
            )
            port_mappings.extend(container_mappings)

        return port_mappings

    async def _get_container_port_mappings(
        self, host_id: str, container_id: str, container_name: str, image: str
    ) -> list[PortMapping]:
        """Get port mappings for a single container."""
        try:
            # Get detailed inspect data for ports
            inspect_info = await self._get_container_inspect_info(host_id, container_id)

            # Get ports from inspect data
            inspect_cmd = f"inspect {container_id}"
            inspect_result = await self.context_manager.execute_docker_command(host_id, inspect_cmd)

            container_inspect_data = self._parse_inspect_result(inspect_result)
            if not container_inspect_data:
                return []

            return self._extract_port_mappings_from_inspect(
                container_inspect_data, container_id, container_name, image, inspect_info
            )

        except Exception as e:
            logger.debug(
                "Failed to get ports for container",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return []

    def _parse_inspect_result(self, inspect_result: Any) -> dict[str, Any] | None:
        """Parse docker inspect result to get container data."""
        if isinstance(inspect_result, list) and len(inspect_result) > 0:
            return inspect_result[0]
        elif isinstance(inspect_result, dict) and "output" in inspect_result:
            try:
                inspect_parsed = json.loads(inspect_result["output"])
                if isinstance(inspect_parsed, list) and len(inspect_parsed) > 0:
                    return inspect_parsed[0]
            except json.JSONDecodeError:
                pass
        return None

    def _extract_port_mappings_from_inspect(
        self,
        container_inspect_data: dict[str, Any],
        container_id: str,
        container_name: str,
        image: str,
        inspect_info: dict[str, Any],
    ) -> list[PortMapping]:
        """Extract port mappings from container inspect data."""
        port_mappings = []

        # Parse NetworkSettings.Ports for structured port data
        network_settings = container_inspect_data.get("NetworkSettings", {})
        ports_data = network_settings.get("Ports", {})

        if not ports_data:
            return port_mappings

        for container_port, host_mappings in ports_data.items():
            if host_mappings:  # Port is exposed to host
                for mapping in host_mappings:
                    host_ip = mapping.get("HostIp", "0.0.0.0")  # nosec B104 - Docker port mapping
                    host_port = mapping.get("HostPort", "")

                    # Parse protocol from container_port (e.g., "80/tcp")
                    container_port_clean, protocol = self._parse_container_port(container_port)

                    port_mapping = PortMapping(
                        host_ip=host_ip,
                        host_port=host_port,
                        container_port=container_port_clean,
                        protocol=protocol,
                        container_id=container_id,
                        container_name=container_name,
                        image=image,
                        compose_project=inspect_info.get("compose_project", "") or None,
                    )
                    port_mappings.append(port_mapping)

        return port_mappings

    def _parse_container_port(self, container_port: str) -> tuple[str, str]:
        """Parse container port string to extract port and protocol."""
        if "/" in container_port:
            port, protocol = container_port.split("/", 1)
            return port, protocol.upper()
        else:
            return container_port, "TCP"

    def _detect_port_conflicts(self, port_mappings: list[PortMapping]) -> list[PortConflict]:
        """Detect port conflicts between containers."""
        conflicts = []
        port_usage = {}  # key: (host_ip, host_port, protocol), value: list of containers

        # Group mappings by port
        for mapping in port_mappings:
            key = (mapping.host_ip, mapping.host_port, mapping.protocol)
            if key not in port_usage:
                port_usage[key] = []
            port_usage[key].append(mapping)

        # Find conflicts (same host port used by multiple containers)
        for (host_ip, host_port, protocol), mappings in port_usage.items():
            if len(mappings) > 1:
                conflict = self._create_port_conflict(host_ip, host_port, protocol, mappings)
                conflicts.append(conflict)

        return conflicts

    def _create_port_conflict(
        self, host_ip: str, host_port: str, protocol: str, mappings: list[PortMapping]
    ) -> PortConflict:
        """Create a port conflict object and mark affected mappings."""
        container_names = []
        container_details = []

        for mapping in mappings:
            mapping.is_conflict = True
            mapping.conflict_with = [
                m.container_name for m in mappings if m.container_id != mapping.container_id
            ]
            container_names.append(mapping.container_name)
            container_details.append({
                "container_id": mapping.container_id,
                "container_name": mapping.container_name,
                "image": mapping.image,
                "compose_project": mapping.compose_project,
            })

        return PortConflict(
            host_port=host_port,
            protocol=protocol,
            host_ip=host_ip,
            affected_containers=container_names,
            container_details=container_details,
        )

    def _generate_port_summary(
        self, port_mappings: list[PortMapping], conflicts: list[PortConflict]
    ) -> dict[str, Any]:
        """Generate summary statistics for port usage."""
        protocol_counts = {}
        port_range_usage = {"0-1023": 0, "1024-49151": 0, "49152-65535": 0}

        for mapping in port_mappings:
            # Count by protocol
            protocol_counts[mapping.protocol] = protocol_counts.get(mapping.protocol, 0) + 1

            # Count by port range
            self._categorize_port_range(mapping.host_port, port_range_usage)

        return {
            "protocol_counts": protocol_counts,
            "port_range_usage": port_range_usage,
            "total_conflicts": len(conflicts),
            "containers_with_conflicts": len(
                set(mapping.container_name for mapping in port_mappings if mapping.is_conflict)
            ),
        }

    def _categorize_port_range(self, host_port: str, port_range_usage: dict[str, int]) -> None:
        """Categorize port into range buckets."""
        try:
            port_num = int(host_port)
            if port_num <= 1023:
                port_range_usage["0-1023"] += 1
            elif port_num <= 49151:
                port_range_usage["1024-49151"] += 1
            else:
                port_range_usage["49152-65535"] += 1
        except ValueError:
            pass
