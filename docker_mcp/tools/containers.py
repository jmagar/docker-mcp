"""Container management MCP tools."""

import asyncio
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import docker.models.containers

import docker
import structlog

from ..constants import (
    DOCKER_COMPOSE_CONFIG_FILES,
    DOCKER_COMPOSE_PROJECT,
)
from ..core.config_loader import DockerMCPConfig
from ..core.docker_context import DockerContextManager
from ..core.error_response import DockerMCPErrorResponse, create_success_response
from ..core.exceptions import DockerCommandError, DockerContextError
from ..models.container import (
    ContainerStats,
    PortConflict,
    PortMapping,
)
from ..models.enums import ProtocolLiteral
from .stacks import StackTools

logger = structlog.get_logger()


class ContainerTools:
    """Container management tools for MCP."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager
        self.stack_tools = StackTools(config, context_manager)

    def _build_error_response(
        self, host_id: str, operation: str, error_message: str, container_id: str | None = None
    ) -> dict[str, Any]:
        """Build standardized error response with container context.

        DEPRECATED: Use DockerMCPErrorResponse methods directly with explicit intent instead.
        This method provides generic fallback for legacy callers.
        """
        context = {"host_id": host_id, "operation": operation}
        if container_id:
            context["container_id"] = container_id
        return DockerMCPErrorResponse.generic_error(error_message, context)

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
            # Get Docker client and list containers using Docker SDK
            client = await self.context_manager.get_client(host_id)
            if client is None:
                error_response = self._build_error_response(
                    host_id, "list_containers", f"Could not connect to Docker on host {host_id}"
                )
                # Add container-specific fields to error response under "data" key
                error_response["data"] = {
                    "containers": [],
                    "pagination": {
                        "total": 0,
                        "limit": limit,
                        "offset": offset,
                        "returned": 0,
                        "has_next": False,
                        "has_prev": offset > 0,
                    },
                }
                return error_response

            docker_containers = await asyncio.to_thread(client.containers.list, all=all_containers)

            # Convert Docker SDK container objects to our format
            containers = []
            for container in docker_containers:
                try:
                    # Use container.attrs directly instead of making redundant inspect calls
                    container_id = container.id[:12]
                    container_data = container.attrs

                    # Extract enhanced info directly from attrs
                    mounts = container_data.get("Mounts", [])
                    network_settings = container_data.get("NetworkSettings", {})
                    labels = container_data.get("Config", {}).get("Labels", {}) or {}

                    # Parse volume mounts
                    volumes = []
                    for mount in mounts:
                        if mount.get("Type") == "bind":
                            volumes.append(
                                f"{mount.get('Source', '')}:{mount.get('Destination', '')}"
                            )
                        elif mount.get("Type") == "volume":
                            volumes.append(
                                f"{mount.get('Name', '')}:{mount.get('Destination', '')}"
                            )

                    # Parse networks
                    networks = list(network_settings.get("Networks", {}).keys())

                    # Extract compose information
                    compose_project = labels.get("com.docker.compose.project", "")
                    compose_file = labels.get("com.docker.compose.config-files", "")

                    # Extract ports from container attributes
                    ports_dict = network_settings.get("Ports", {})
                    ports_str = self._format_ports_from_dict(ports_dict)

                    # Return enhanced container info
                    container_summary = {
                        "id": container_id,
                        "name": container.name,
                        "image": container_data.get("Config", {}).get("Image", ""),
                        "status": container.status,
                        "state": container_data.get("State", {}).get("Status", ""),
                        "ports": self._parse_ports_summary(ports_str),
                        "host_id": host_id,
                        "volumes": volumes,
                        "networks": networks,
                        "compose_project": compose_project,
                        "compose_file": compose_file,
                    }
                    containers.append(container_summary)
                except Exception as e:
                    logger.warning(
                        "Failed to process container", container_id=container.id, error=str(e)
                    )

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

            return create_success_response(
                data={
                    "containers": paginated_containers,
                    "pagination": {
                        "total": total_count,
                        "limit": limit,
                        "offset": offset,
                        "returned": len(paginated_containers),
                        "has_next": (offset + limit) < total_count,
                        "has_prev": offset > 0,
                    },
                },
                context={"host_id": host_id, "operation": "list_containers"},
            )

        except (DockerCommandError, DockerContextError) as e:
            logger.error("Failed to list containers", host_id=host_id, error=str(e))
            return DockerMCPErrorResponse.generic_error(
                str(e),
                {
                    "host_id": host_id,
                    "operation": "list_containers",
                    "data": {
                        "containers": [],
                        "total": 0,
                        "pagination": {
                            "total": 0,
                            "limit": limit,
                            "offset": offset,
                            "returned": 0,
                            "has_next": False,
                            "has_prev": offset > 0,
                        },
                    },
                },
            )

    async def get_container_info(self, host_id: str, container_id: str) -> dict[str, Any]:
        """Get detailed information about a specific container.

        Args:
            host_id: ID of the Docker host
            container_id: Container ID or name

        Returns:
            Detailed container information
        """
        try:
            client = await self.context_manager.get_client(host_id)
            if client is None:
                return self._build_error_response(
                    host_id,
                    "get_container_info",
                    f"Could not connect to Docker on host {host_id}",
                    container_id,
                )

            # Use Docker SDK to get container
            container = await asyncio.to_thread(client.containers.get, container_id)

            # Get container attributes (equivalent to inspect data)
            container_data = container.attrs

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
            compose_project = labels.get(DOCKER_COMPOSE_PROJECT, "")
            compose_file = labels.get(DOCKER_COMPOSE_CONFIG_FILES, "")

            logger.info("Retrieved container info", host_id=host_id, container_id=container_id)

            return create_success_response(
                data={
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
                },
                context={
                    "host_id": host_id,
                    "operation": "get_container_info",
                    "container_id": container_id,
                },
            )

        except docker.errors.NotFound:
            logger.error("Container not found", host_id=host_id, container_id=container_id)
            return DockerMCPErrorResponse.container_not_found(host_id, container_id)
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error getting container info",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return DockerMCPErrorResponse.docker_command_error(
                host_id,
                f"inspect {container_id}",
                getattr(e, "response", {}).get("status_code", 500),
                str(e),
            )
        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to get container info",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return DockerMCPErrorResponse.generic_error(
                str(e),
                {
                    "host_id": host_id,
                    "operation": "get_container_info",
                    "container_id": container_id,
                },
            )

    async def start_container(self, host_id: str, container_id: str) -> dict[str, Any]:
        """Start a container on a Docker host.

        Args:
            host_id: ID of the Docker host
            container_id: Container ID or name to start

        Returns:
            Operation result
        """
        try:
            client = await self.context_manager.get_client(host_id)
            if client is None:
                return self._build_error_response(
                    host_id,
                    "start_container",
                    f"Could not connect to Docker on host {host_id}",
                    container_id,
                )

            # Get container and start it using Docker SDK
            container = await asyncio.to_thread(client.containers.get, container_id)
            await asyncio.to_thread(container.start)

            logger.info("Container started", host_id=host_id, container_id=container_id)

            return create_success_response(
                message=f"Container {container_id} started successfully",
                data={
                    "container_id": container_id,
                    "host_id": host_id,
                },
                context={
                    "host_id": host_id,
                    "operation": "start_container",
                    "container_id": container_id,
                },
            )

        except docker.errors.NotFound:
            logger.error("Container not found", host_id=host_id, container_id=container_id)
            return DockerMCPErrorResponse.container_not_found(host_id, container_id)
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error starting container",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return DockerMCPErrorResponse.docker_command_error(
                host_id,
                f"start {container_id}",
                getattr(e, "response", {}).get("status_code", 500),
                str(e),
            )
        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to start container",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return DockerMCPErrorResponse.generic_error(
                f"Failed to start container {container_id}: {str(e)}",
                {"host_id": host_id, "operation": "start_container", "container_id": container_id},
            )

    async def stop_container(
        self, host_id: str, container_id: str, timeout: int = 30
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
            client = await self.context_manager.get_client(host_id)
            if client is None:
                return DockerMCPErrorResponse.docker_context_error(
                    host_id=host_id,
                    operation="stop_container",
                    cause=f"Could not connect to Docker on host {host_id}"
                )

            # Get container and stop it using Docker SDK
            container = await asyncio.to_thread(client.containers.get, container_id)
            await asyncio.to_thread(lambda: container.stop(timeout=timeout))

            logger.info(
                "Container stopped", host_id=host_id, container_id=container_id, timeout=timeout
            )
            return create_success_response(
                message=f"Container {container_id} stopped successfully",
                data={
                    "container_id": container_id,
                    "host_id": host_id,
                },
                context={
                    "host_id": host_id,
                    "operation": "stop_container",
                    "container_id": container_id,
                },
            )

        except docker.errors.NotFound:
            logger.error("Container not found", host_id=host_id, container_id=container_id)
            return DockerMCPErrorResponse.container_not_found(host_id, container_id)
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error stopping container",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return DockerMCPErrorResponse.docker_command_error(
                host_id,
                f"stop {container_id}",
                getattr(e, "response", {}).get("status_code", 500),
                str(e),
            )
        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to stop container", host_id=host_id, container_id=container_id, error=str(e)
            )
            return self._build_error_response(
                host_id,
                "stop_container",
                f"Failed to stop container {container_id}: {str(e)}",
                container_id,
            )
        except Exception as e:
            # Catch network/timeout errors like "fetch failed"
            logger.error(
                "Unexpected error stopping container",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return DockerMCPErrorResponse.generic_error(
                "Network or timeout error stopping container",
                {
                    "host_id": host_id,
                    "operation": "stop_container",
                    "container_id": container_id,
                    "cause": str(e),
                },
            )

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
            client = await self.context_manager.get_client(host_id)
            if client is None:
                return self._build_error_response(
                    host_id,
                    "restart_container",
                    f"Could not connect to Docker on host {host_id}",
                    container_id,
                )

            # Get container and restart it using Docker SDK
            container = await asyncio.to_thread(client.containers.get, container_id)
            await asyncio.to_thread(lambda: container.restart(timeout=timeout))

            logger.info(
                "Container restarted", host_id=host_id, container_id=container_id, timeout=timeout
            )
            return create_success_response(
                message=f"Container {container_id} restarted successfully",
                data={
                    "container_id": container_id,
                    "host_id": host_id,
                },
                context={
                    "host_id": host_id,
                    "operation": "restart_container",
                    "container_id": container_id,
                },
            )

        except docker.errors.NotFound:
            logger.error("Container not found", host_id=host_id, container_id=container_id)
            return DockerMCPErrorResponse.container_not_found(host_id, container_id)
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error restarting container",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return DockerMCPErrorResponse.docker_command_error(
                host_id,
                f"restart {container_id}",
                getattr(e, "response", {}).get("status_code", 500),
                str(e),
            )
        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to restart container",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return self._build_error_response(
                host_id,
                "restart_container",
                f"Failed to restart container {container_id}: {str(e)}",
                container_id,
            )

    async def get_container_stats(self, host_id: str, container_id: str) -> dict[str, Any]:
        """Get resource statistics for a container.

        Args:
            host_id: ID of the Docker host
            container_id: Container ID or name

        Returns:
            Container resource statistics
        """
        try:
            client = await self.context_manager.get_client(host_id)
            if client is None:
                return self._build_error_response(
                    host_id,
                    "get_container_stats",
                    f"Could not connect to Docker on host {host_id}",
                    container_id,
                )

            # Get container and retrieve stats using Docker SDK
            container = await asyncio.to_thread(client.containers.get, container_id)

            # Docker SDK returns a single snapshot dict when stream=False
            stats_raw = await asyncio.to_thread(lambda: container.stats(stream=False))

            # Parse stats data from Docker SDK format (different from CLI format)
            cpu_stats = stats_raw.get("cpu_stats", {})
            memory_stats = stats_raw.get("memory_stats", {})
            networks = stats_raw.get("networks", {})
            blkio_stats = stats_raw.get("blkio_stats", {})
            pids_stats = stats_raw.get("pids_stats", {})

            # Calculate CPU percentage from Docker SDK data
            cpu_percent = self._calculate_cpu_percentage(
                cpu_stats, stats_raw.get("precpu_stats", {})
            )

            # Memory stats
            memory_usage = memory_stats.get("usage", 0)
            memory_limit = memory_stats.get("limit", 0)
            memory_percent = (memory_usage / memory_limit * 100) if memory_limit > 0 else 0

            # Network stats (sum all interfaces)
            net_rx = sum(net.get("rx_bytes", 0) for net in networks.values())
            net_tx = sum(net.get("tx_bytes", 0) for net in networks.values())

            # Block I/O stats
            blk_read = sum(
                stat.get("value", 0)
                for stat in blkio_stats.get("io_service_bytes_recursive", [])
                if stat.get("op") == "read"
            )
            blk_write = sum(
                stat.get("value", 0)
                for stat in blkio_stats.get("io_service_bytes_recursive", [])
                if stat.get("op") == "write"
            )

            stats = ContainerStats(
                container_id=container_id,
                host_id=host_id,
                cpu_percentage=cpu_percent,
                memory_usage=memory_usage,
                memory_limit=memory_limit,
                memory_percentage=memory_percent,
                network_rx=net_rx,
                network_tx=net_tx,
                block_read=blk_read,
                block_write=blk_write,
                pids=pids_stats.get("current", 0),
            )

            logger.debug("Retrieved container stats", host_id=host_id, container_id=container_id)

            return create_success_response(
                message=f"Container {container_id} stats retrieved successfully",
                data=stats.model_dump(),
                context={
                    "host_id": host_id,
                    "operation": "get_container_stats",
                    "container_id": container_id,
                },
            )

        except docker.errors.NotFound:
            logger.error(
                "Container not found for stats", host_id=host_id, container_id=container_id
            )
            return DockerMCPErrorResponse.container_not_found(host_id, container_id)
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error getting container stats",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return DockerMCPErrorResponse.generic_error(
                f"Failed to get stats: {str(e)}",
                {
                    "host_id": host_id,
                    "operation": "get_container_stats",
                    "container_id": container_id,
                },
            )
        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to get container stats",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return DockerMCPErrorResponse.generic_error(
                str(e),
                {
                    "host_id": host_id,
                    "operation": "get_container_stats",
                    "container_id": container_id,
                },
            )

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

    def _format_ports_from_dict(self, ports_dict: dict[str, Any]) -> str:
        """Convert Docker SDK ports dictionary to string format for _parse_ports_summary."""
        if not ports_dict:
            return ""

        port_strings = []
        for container_port, host_bindings in ports_dict.items():
            if host_bindings:  # Only include ports that are actually bound
                for binding in host_bindings:
                    host_ip = binding.get("HostIp", "0.0.0.0")
                    host_port = binding.get("HostPort", "")
                    if host_port:
                        port_strings.append(f"{host_ip}:{host_port}->{container_port}")

        return ", ".join(port_strings)

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

    def _calculate_cpu_percentage(self, cpu_stats: dict, precpu_stats: dict) -> float:
        """Calculate CPU percentage from Docker SDK stats data."""
        cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - precpu_stats.get(
            "cpu_usage", {}
        ).get("total_usage", 0)

        system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get(
            "system_cpu_usage", 0
        )

        online_cpus = cpu_stats.get("online_cpus", 1)

        if system_delta > 0 and cpu_delta >= 0:
            return (cpu_delta / system_delta) * online_cpus * 100.0
        return 0.0

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
            client = await self.context_manager.get_client(host_id)
            if client is None:
                return {"volumes": [], "networks": [], "compose_project": "", "compose_file": ""}

            # Use Docker SDK to get container
            container = await asyncio.to_thread(client.containers.get, container_id)

            # Return container attributes which contain all inspect data
            container_data = container.attrs

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
            compose_project = labels.get(DOCKER_COMPOSE_PROJECT, "")
            compose_file = labels.get(DOCKER_COMPOSE_CONFIG_FILES, "")

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
            error_response = self._build_error_response(
                host_id,
                "manage_container",
                f"Invalid action '{action}'. Valid actions: {', '.join(valid_actions)}",
                container_id,
            )
            error_response.update({"action": action})
            return error_response

        try:
            # Build command based on action
            cmd = self._build_container_command(action, container_id, force, timeout)

            await self.context_manager.execute_docker_command(host_id, cmd)

            logger.info(
                "Container action completed",
                host_id=host_id,
                container_id=container_id,
                action=action,
                force=force,
            )

            past_tense = {
                "start": "started",
                "stop": "stopped",
                "restart": "restarted",
                "pause": "paused",
                "unpause": "unpaused",
                "remove": "removed",
            }

            return create_success_response(
                message=f"Container {container_id} {past_tense[action]} successfully",
                data={
                    "container_id": container_id,
                    "host_id": host_id,
                },
                context={
                    "host_id": host_id,
                    "operation": "manage_container",
                    "container_id": container_id,
                    "action": action,
                },
            )

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                f"Failed to {action} container",
                host_id=host_id,
                container_id=container_id,
                action=action,
                error=str(e),
            )
            error_response = self._build_error_response(
                host_id,
                "manage_container",
                f"Failed to {action} container {container_id}: {str(e)}",
                container_id,
            )
            error_response.update(
                {
                    "action": action,
                    "force": force,
                    "timeout": timeout if action in ["stop", "restart"] else None,
                }
            )
            return error_response

    async def pull_image(self, host_id: str, image_name: str) -> dict[str, Any]:
        """Pull a Docker image on a remote host.

        Args:
            host_id: ID of the Docker host
            image_name: Name of the Docker image to pull (e.g., nginx:latest, ubuntu:20.04)

        Returns:
            Operation result
        """
        try:
            client = await self.context_manager.get_client(host_id)
            if client is None:
                return self._build_error_response(
                    host_id, "pull_image", f"Could not connect to Docker on host {host_id}"
                )

            # Pull image using Docker SDK
            image = await asyncio.to_thread(client.images.pull, image_name)

            logger.info(
                "Image pull completed",
                host_id=host_id,
                image_name=image_name,
                image_id=image.id[:12],
            )

            return create_success_response(
                message=f"Successfully pulled image {image_name}",
                data={
                    "image_name": image_name,
                    "image_id": image.id[:12],
                    "host_id": host_id,
                },
                context={"host_id": host_id, "operation": "pull_image"},
            )

        except docker.errors.ImageNotFound:
            logger.error("Image not found", host_id=host_id, image_name=image_name)
            return self._build_error_response(
                host_id, "pull_image", f"Image {image_name} not found"
            )
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error pulling image",
                host_id=host_id,
                image_name=image_name,
                error=str(e),
            )
            return self._build_error_response(
                host_id, "pull_image", f"Failed to pull image: {str(e)}"
            )
        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to pull image",
                host_id=host_id,
                image_name=image_name,
                error=str(e),
            )
            return self._build_error_response(
                host_id, "pull_image", f"Failed to pull image {image_name}: {str(e)}"
            )

    def _build_container_command(
        self, action: str, container_id: str, force: bool, timeout: int
    ) -> str:
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
            return f"unsupported_action_{action}"

    async def list_host_ports(self, host_id: str) -> dict[str, Any]:
        """List all ports currently in use by containers on a Docker host (includes stopped containers).

        Args:
            host_id: ID of the Docker host

        Returns:
            Comprehensive port usage information with conflict detection
        """
        try:
            # Get container data (always include stopped containers)
            containers = await self._get_containers_for_port_analysis(host_id, include_stopped=True)

            # Collect all port mappings from containers
            port_mappings = await self._collect_port_mappings(host_id, containers)

            # Detect and mark port conflicts
            conflicts = self._detect_port_conflicts(port_mappings)

            # Generate summary statistics
            summary = self._generate_port_summary(port_mappings, conflicts)

            total_containers = len(containers)

            logger.info(
                "Listed host ports",
                host_id=host_id,
                total_ports=len(port_mappings),
                total_containers=total_containers,
                conflicts=len(conflicts),
            )

            return create_success_response(
                message="Host ports retrieved successfully",
                data={
                    "host_id": host_id,
                    "total_ports": len(port_mappings),
                    "total_containers": total_containers,
                    "port_mappings": [mapping.model_dump() for mapping in port_mappings],
                    "conflicts": [conflict.model_dump() for conflict in conflicts],
                    "summary": summary,
                    "cached": False,
                },
                context={"host_id": host_id, "operation": "list_host_ports"},
            )

        except (DockerCommandError, DockerContextError) as e:
            logger.error("Failed to list host ports", host_id=host_id, error=str(e))
            return self._build_error_response(host_id, "list_host_ports", str(e))
        except Exception as e:
            logger.error("Unexpected error listing host ports", host_id=host_id, error=str(e))
            return self._build_error_response(
                host_id, "list_host_ports", f"Failed to list ports: {e}"
            )

    async def _get_containers_for_port_analysis(
        self, host_id: str, include_stopped: bool
    ) -> list["docker.models.containers.Container"]:
        """Get container data for port analysis."""
        # Get Docker client and list containers using Docker SDK
        client = await self.context_manager.get_client(host_id)
        if client is None:
            return []

        # Use asyncio.to_thread to run the blocking Docker SDK call off the event loop
        docker_containers = await asyncio.to_thread(client.containers.list, all=include_stopped)

        # Return Docker SDK container objects directly for more efficient port extraction
        return docker_containers

    async def _collect_port_mappings(
        self, host_id: str, containers: list["docker.models.containers.Container"]
    ) -> list[PortMapping]:
        """Collect port mappings from all containers."""
        port_mappings = []

        for container in containers:
            container_id = container.id[:12]
            container_name = container.name
            image = container.attrs.get("Config", {}).get("Image", "")

            # Extract port mappings directly from Docker SDK container object
            container_mappings = self._extract_port_mappings_from_container(
                container, container_id, container_name, image, host_id
            )
            port_mappings.extend(container_mappings)

        return port_mappings

    def _extract_port_mappings_from_container(
        self,
        container: "docker.models.containers.Container",
        container_id: str,
        container_name: str,
        image: str,
        host_id: str,
    ) -> list[PortMapping]:
        """Extract port mappings directly from Docker SDK container object."""
        port_mappings = []

        # Get port mappings from container.ports (Docker SDK provides this directly)
        ports_data = container.ports

        if not ports_data:
            return port_mappings

        for container_port, host_mappings in ports_data.items():
            if host_mappings:  # Port is exposed to host
                for mapping in host_mappings:
                    host_ip = mapping.get("HostIp", "0.0.0.0")  # nosec B104 - Docker port mapping
                    host_port = mapping.get("HostPort", "")

                    # Parse protocol from container_port (e.g., "80/tcp")
                    container_port_clean, protocol = self._parse_container_port(container_port)

                    # Get compose project from container labels
                    labels = container.labels or {}
                    compose_project = labels.get(DOCKER_COMPOSE_PROJECT, "")

                    # Skip non-numeric ports instead of creating invalid mappings with port "0"
                    if host_port.isdigit() and container_port_clean.isdigit():
                        port_mapping = PortMapping(
                            host_id=host_id,
                            host_ip=host_ip,
                            host_port=int(host_port),
                            container_port=int(container_port_clean),
                            protocol=protocol,
                            container_id=container_id,
                            container_name=container_name,
                            image=image,
                            compose_project=compose_project,
                            is_conflict=False,
                            conflict_with=[],
                        )
                        port_mappings.append(port_mapping)

        return port_mappings

    def _parse_container_port(self, container_port: str) -> tuple[str, ProtocolLiteral]:
        """Parse container port string to extract port and protocol."""
        if "/" in container_port:
            port, proto = container_port.split("/", 1)
        else:
            port, proto = container_port, "tcp"
        proto_lc = proto.lower()
        if proto_lc not in ("tcp", "udp", "sctp"):
            proto_lc = "tcp"
        return port, cast(ProtocolLiteral, proto_lc)

    def _detect_port_conflicts(self, port_mappings: list[PortMapping]) -> list[PortConflict]:
        """Detect port conflicts between containers."""
        conflicts: list[PortConflict] = []
        # key: (host_ip, host_port, protocol), value: list of containers
        port_usage: dict[tuple[str, int, ProtocolLiteral], list[PortMapping]] = {}

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
        self, host_ip: str, host_port: int, protocol: ProtocolLiteral, mappings: list[PortMapping]
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
            container_details.append(
                {
                    "container_id": mapping.container_id,
                    "container_name": mapping.container_name,
                    "image": mapping.image,
                    "compose_project": mapping.compose_project,
                }
            )

        return PortConflict(
            host_id=mappings[0].host_id,  # All mappings should have the same host_id
            host_port=str(host_port),
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
            self._categorize_port_range(str(mapping.host_port), port_range_usage)

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
