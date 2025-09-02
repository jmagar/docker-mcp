"""Container management MCP tools."""

import asyncio
from datetime import datetime
from typing import Any

import docker
import structlog

from ..constants import (
    DOCKER_COMPOSE_CONFIG_FILES,
    DOCKER_COMPOSE_PROJECT,
    DOCKER_COMPOSE_SERVICE,
)
from .stacks import StackTools
from ..core.config_loader import DockerMCPConfig
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
            # Get Docker client and list containers using Docker SDK
            client = await self.context_manager.get_client(host_id)
            loop = asyncio.get_event_loop()
            docker_containers = await loop.run_in_executor(
                None, lambda: client.containers.list(all=all_containers)
            )

            # Convert Docker SDK container objects to our format
            containers = []
            for container in docker_containers:
                try:
                    # Get enhanced container info including inspect data
                    container_id = container.id[:12]
                    inspect_info = await self._get_container_inspect_info(host_id, container_id)

                    # Extract ports from container attributes
                    ports_dict = container.attrs.get("NetworkSettings", {}).get("Ports", {})
                    ports_str = self._format_ports_from_dict(ports_dict)

                    # Return enhanced container info
                    container_summary = {
                        "id": container_id,
                        "name": container.name,
                        "image": container.attrs.get("Config", {}).get("Image", ""),
                        "status": container.status,
                        "state": container.attrs.get("State", {}).get("Status", ""),
                        "ports": self._parse_ports_summary(ports_str),
                        "host_id": host_id,
                        "volumes": inspect_info.get("volumes", []),
                        "networks": inspect_info.get("networks", []),
                        "compose_project": inspect_info.get("compose_project", ""),
                        "compose_file": inspect_info.get("compose_file", ""),
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
            client = await self.context_manager.get_client(host_id)
            loop = asyncio.get_event_loop()

            # Use Docker SDK to get container
            container = await loop.run_in_executor(
                None, lambda: client.containers.get(container_id)
            )

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

        except docker.errors.NotFound:
            logger.error("Container not found", host_id=host_id, container_id=container_id)
            return {"error": f"Container {container_id} not found"}
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error getting container info",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {"error": f"Docker API error: {str(e)}"}
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
            client = await self.context_manager.get_client(host_id)
            loop = asyncio.get_event_loop()

            # Get container and start it using Docker SDK
            container = await loop.run_in_executor(
                None, lambda: client.containers.get(container_id)
            )
            await loop.run_in_executor(None, container.start)

            logger.info("Container started", host_id=host_id, container_id=container_id)
            return {
                "success": True,
                "message": f"Container {container_id} started successfully",
                "container_id": container_id,
                "host_id": host_id,
                "timestamp": datetime.now().isoformat(),
            }

        except docker.errors.NotFound:
            logger.error("Container not found", host_id=host_id, container_id=container_id)
            return {"success": False, "error": f"Container {container_id} not found"}
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error starting container",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {"success": False, "error": f"Failed to start container: {str(e)}"}
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
            loop = asyncio.get_event_loop()

            # Get container and stop it using Docker SDK
            container = await loop.run_in_executor(
                None, lambda: client.containers.get(container_id)
            )
            await loop.run_in_executor(None, lambda: container.stop(timeout=timeout))

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

        except docker.errors.NotFound:
            logger.error("Container not found", host_id=host_id, container_id=container_id)
            return {"success": False, "error": f"Container {container_id} not found"}
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error stopping container",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {"success": False, "error": f"Failed to stop container: {str(e)}"}
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
        except Exception as e:
            # Catch network/timeout errors like "fetch failed"
            logger.error(
                "Unexpected error stopping container",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return {
                "success": False,
                "message": f"Network or timeout error stopping container {container_id}: {str(e)}",
                "container_id": container_id,
                "host_id": host_id,
                "error": str(e),
                "error_type": type(e).__name__,
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
            client = await self.context_manager.get_client(host_id)
            loop = asyncio.get_event_loop()

            # Get container and restart it using Docker SDK
            container = await loop.run_in_executor(
                None, lambda: client.containers.get(container_id)
            )
            await loop.run_in_executor(None, lambda: container.restart(timeout=timeout))

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

        except docker.errors.NotFound:
            logger.error("Container not found", host_id=host_id, container_id=container_id)
            return {"success": False, "error": f"Container {container_id} not found"}
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error restarting container",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {"success": False, "error": f"Failed to restart container: {str(e)}"}
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
            client = await self.context_manager.get_client(host_id)
            loop = asyncio.get_event_loop()

            # Get container and retrieve stats using Docker SDK
            container = await loop.run_in_executor(
                None, lambda: client.containers.get(container_id)
            )

            # Get stats (stream=False for single stats snapshot)
            stats_generator = await loop.run_in_executor(
                None, lambda: container.stats(stream=False)
            )

            # Extract stats from the generator - it returns one item when stream=False
            stats_raw = next(iter(stats_generator))

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
            return stats.model_dump()

        except docker.errors.NotFound:
            logger.error(
                "Container not found for stats", host_id=host_id, container_id=container_id
            )
            return {"error": f"Container {container_id} not found"}
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error getting container stats",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {"error": f"Failed to get stats: {str(e)}"}
        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to get container stats",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {"error": str(e)}

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
            loop = asyncio.get_event_loop()

            # Use Docker SDK to get container
            container = await loop.run_in_executor(
                None, lambda: client.containers.get(container_id)
            )

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
        valid_actions = ["start", "stop", "restart", "pause", "unpause", "remove", "build"]
        if action not in valid_actions:
            return {
                "success": False,
                "error": f"Invalid action '{action}'. Valid actions: {', '.join(valid_actions)}",
                "container_id": container_id,
                "host_id": host_id,
                "timestamp": datetime.now().isoformat(),
            }

        try:
            if action == "build":
                # Build via docker compose for the service this container belongs to
                client = await self.context_manager.get_client(host_id)
                loop = asyncio.get_event_loop()
                container = await loop.run_in_executor(
                    None, lambda: client.containers.get(container_id)
                )
                labels = container.labels or container.attrs.get("Config", {}).get("Labels", {}) or {}
                project = labels.get(DOCKER_COMPOSE_PROJECT)
                service = labels.get(DOCKER_COMPOSE_SERVICE)

                if not project or not service:
                    return {
                        "success": False,
                        "error": "Container is not part of a compose project; cannot build",
                        "container_id": container_id,
                        "host_id": host_id,
                    }

                # Use StackTools to run compose build for the specific service
                stack_tools = StackTools(self.config, self.context_manager)
                options = {"services": [service]}
                build_result = await stack_tools.manage_stack(host_id, project, "build", options)

                if build_result.get("success"):
                    logger.info(
                        "Container build completed via compose",
                        host_id=host_id,
                        container_id=container_id,
                        project=project,
                        service=service,
                    )
                    return {
                        "success": True,
                        "message": f"Service '{service}' in project '{project}' built successfully",
                        "container_id": container_id,
                        "host_id": host_id,
                        "project": project,
                        "service": service,
                        "action": action,
                        "timestamp": datetime.now().isoformat(),
                    }
                else:
                    return {
                        "success": False,
                        "error": build_result.get("error", "Build failed"),
                        "container_id": container_id,
                        "host_id": host_id,
                        "project": project,
                        "service": service,
                        "action": action,
                    }
            else:
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
            loop = asyncio.get_event_loop()

            # Pull image using Docker SDK
            image = await loop.run_in_executor(None, lambda: client.images.pull(image_name))

            logger.info(
                "Image pull completed",
                host_id=host_id,
                image_name=image_name,
                image_id=image.id[:12],
            )

            return {
                "success": True,
                "message": f"Successfully pulled image {image_name}",
                "image_name": image_name,
                "image_id": image.id[:12],
                "host_id": host_id,
                "image_tags": image.tags,
                "timestamp": datetime.now().isoformat(),
            }

        except docker.errors.ImageNotFound:
            logger.error("Image not found", host_id=host_id, image_name=image_name)
            return {"success": False, "error": f"Image {image_name} not found"}
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error pulling image",
                host_id=host_id,
                image_name=image_name,
                error=str(e),
            )
            return {"success": False, "error": f"Failed to pull image: {str(e)}"}
        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to pull image",
                host_id=host_id,
                image_name=image_name,
                error=str(e),
            )
            return {
                "success": False,
                "message": f"Failed to pull image {image_name}: {str(e)}",
                "image_name": image_name,
                "host_id": host_id,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }

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
            raise ValueError(f"Unknown action: {action}")

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

            return {
                "success": True,
                "host_id": host_id,
                "total_ports": len(port_mappings),
                "total_containers": total_containers,
                "port_mappings": [mapping.model_dump() for mapping in port_mappings],
                "conflicts": [conflict.model_dump() for conflict in conflicts],
                "summary": summary,
                "timestamp": datetime.now().isoformat(),
                "cached": False,
            }

        except (DockerCommandError, DockerContextError) as e:
            logger.error("Failed to list host ports", host_id=host_id, error=str(e))
            raise
        except Exception as e:
            logger.error("Unexpected error listing host ports", host_id=host_id, error=str(e))
            raise DockerCommandError(f"Failed to list ports: {e}") from e

    async def _get_containers_for_port_analysis(
        self, host_id: str, include_stopped: bool
    ) -> list[dict[str, Any]]:
        """Get container data for port analysis."""
        # Get Docker client and list containers using Docker SDK
        client = await self.context_manager.get_client(host_id)
        loop = asyncio.get_event_loop()
        docker_containers = await loop.run_in_executor(
            None, lambda: client.containers.list(all=include_stopped)
        )

        # Return Docker SDK container objects directly for more efficient port extraction
        return docker_containers

    async def _collect_port_mappings(self, host_id: str, containers) -> list[PortMapping]:
        """Collect port mappings from all containers."""
        port_mappings = []

        for container in containers:
            container_id = container.id[:12]
            container_name = container.name
            image = container.attrs.get("Config", {}).get("Image", "")

            # Extract port mappings directly from Docker SDK container object
            container_mappings = self._extract_port_mappings_from_container(
                container, container_id, container_name, image
            )
            port_mappings.extend(container_mappings)

        return port_mappings

    def _extract_port_mappings_from_container(
        self, container, container_id: str, container_name: str, image: str
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

                    port_mapping = PortMapping(
                        host_ip=host_ip,
                        host_port=host_port,
                        container_port=container_port_clean,
                        protocol=protocol.upper(),
                        container_id=container_id,
                        container_name=container_name,
                        image=image,
                        compose_project=compose_project,
                        is_conflict=False,
                        conflict_with=[],
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
            container_details.append(
                {
                    "container_id": mapping.container_id,
                    "container_name": mapping.container_name,
                    "image": mapping.image,
                    "compose_project": mapping.compose_project,
                }
            )

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
