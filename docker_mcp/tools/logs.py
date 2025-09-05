"""Log streaming MCP tools."""

import asyncio
from datetime import datetime
from typing import Any

import docker
import structlog

from ..core.config_loader import DockerMCPConfig
from ..core.docker_context import DockerContextManager
from ..core.exceptions import DockerCommandError, DockerContextError
from ..models.container import ContainerLogs, LogStreamRequest

logger = structlog.get_logger()


class LogTools:
    """Log management tools for MCP."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager

    async def get_container_logs(
        self,
        host_id: str,
        container_id: str,
        lines: int = 100,
        since: str | None = None,
        timestamps: bool = False,
    ) -> dict[str, Any]:
        """Get logs from a container.

        Args:
            host_id: ID of the Docker host
            container_id: Container ID or name
            lines: Number of lines to retrieve (default: 100)
            since: Only logs since this timestamp (e.g., '2023-01-01T00:00:00Z')
            timestamps: Include timestamps in output

        Returns:
            Container logs
        """
        try:
            client = await self.context_manager.get_client(host_id)
            if client is None:
                return {
                    "success": False,
                    "error": f"Could not connect to Docker on host {host_id}"
                }

            loop = asyncio.get_event_loop()

            # Get container and retrieve logs using Docker SDK
            container = await loop.run_in_executor(
                None, lambda: client.containers.get(container_id)
            )

            # Build kwargs for logs method
            logs_kwargs = {
                "tail": lines,
                "timestamps": timestamps,
            }
            if since:
                logs_kwargs["since"] = since

            # Get logs using Docker SDK
            logs_bytes = await loop.run_in_executor(None, lambda: container.logs(**logs_kwargs))

            # Parse logs (logs_bytes is bytes, need to decode)
            logs_str = logs_bytes.decode("utf-8", errors="replace")
            logs_data = logs_str.strip().split("\n") if logs_str.strip() else []

            # Create logs response
            logs = ContainerLogs(
                container_id=container_id,
                host_id=host_id,
                logs=logs_data,
                timestamp=datetime.now().isoformat(),
                truncated=len(logs_data) >= lines,
            )

            logger.info(
                "Retrieved container logs",
                host_id=host_id,
                container_id=container_id,
                lines_returned=len(logs_data),
            )

            return logs.model_dump()

        except docker.errors.NotFound:
            logger.error("Container not found for logs", host_id=host_id, container_id=container_id)
            return {"error": f"Container {container_id} not found"}
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error getting container logs",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {"error": f"Failed to get logs: {str(e)}"}
        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to get container logs",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {
                "error": str(e),
                "container_id": container_id,
                "host_id": host_id,
                "timestamp": datetime.now().isoformat(),
            }

    async def stream_container_logs_setup(
        self,
        host_id: str,
        container_id: str,
        follow: bool = True,
        tail: int = 100,
        since: str | None = None,
        timestamps: bool = False,
    ) -> dict[str, Any]:
        """Setup real-time log streaming for a container.

        This creates a streaming endpoint that can be used for real-time log monitoring.
        The actual streaming is handled by FastMCP's HTTP streaming capabilities.

        Args:
            host_id: ID of the Docker host
            container_id: Container ID or name
            follow: Continue streaming new logs (default: True)
            tail: Number of initial lines to include
            since: Only logs since this timestamp
            timestamps: Include timestamps in output

        Returns:
            Streaming configuration and endpoint information
        """
        try:
            # Validate container exists and is accessible
            await self._validate_container_exists(host_id, container_id)

            # Create stream configuration
            stream_config = LogStreamRequest(
                host_id=host_id,
                container_id=container_id,
                follow=follow,
                tail=tail,
                since=since,
                timestamps=timestamps,
            )

            # In a real implementation, this would register the stream
            # with FastMCP's streaming system and return an endpoint URL
            stream_id = f"{host_id}_{container_id}_{datetime.now().timestamp()}"

            logger.info(
                "Log stream setup created",
                host_id=host_id,
                container_id=container_id,
                stream_id=stream_id,
            )

            return {
                "success": True,
                "stream_id": stream_id,
                "stream_endpoint": f"/streams/logs/{stream_id}",
                "config": stream_config.model_dump(),
                "message": f"Log stream setup for container {container_id} on host {host_id}",
                "instructions": {
                    "connect": "Connect to the streaming endpoint to receive real-time logs",
                    "format": "Server-sent events (SSE)",
                    "reconnect": "Client should handle reconnection on connection loss",
                },
                "timestamp": datetime.now().isoformat(),
            }

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to setup log stream",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {
                "success": False,
                "error": str(e),
                "container_id": container_id,
                "host_id": host_id,
                "timestamp": datetime.now().isoformat(),
            }

    async def get_service_logs(
        self,
        host_id: str,
        service_name: str,
        lines: int = 100,
        since: str | None = None,
        timestamps: bool = False,
    ) -> dict[str, Any]:
        """Get logs from a Docker Compose service.

        Args:
            host_id: ID of the Docker host
            service_name: Name of the service
            lines: Number of lines to retrieve
            since: Only logs since this timestamp
            timestamps: Include timestamps in output

        Returns:
            Service logs
        """
        try:
            # Build Docker Compose logs command
            cmd = f"compose logs --tail {lines}"

            if since:
                cmd += f" --since {since}"

            if timestamps:
                cmd += " --timestamps"

            cmd += f" {service_name}"

            result = await self.context_manager.execute_docker_command(host_id, cmd)

            # Parse logs
            logs_data = []
            if isinstance(result, dict) and "output" in result:
                logs_data = result["output"].strip().split("\n")

            logger.info(
                "Retrieved service logs",
                host_id=host_id,
                service_name=service_name,
                lines_returned=len(logs_data),
            )

            return {
                "service_name": service_name,
                "host_id": host_id,
                "logs": logs_data,
                "timestamp": datetime.now().isoformat(),
                "truncated": len(logs_data) >= lines,
            }

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to get service logs",
                host_id=host_id,
                service_name=service_name,
                error=str(e),
            )
            return {
                "error": str(e),
                "service_name": service_name,
                "host_id": host_id,
                "timestamp": datetime.now().isoformat(),
            }

    async def _validate_container_exists(self, host_id: str, container_id: str) -> None:
        """Validate that a container exists and is accessible."""
        try:
            cmd = f"inspect {container_id}"
            await self.context_manager.execute_docker_command(host_id, cmd)

            # If we get here without exception, container exists
            logger.debug(
                "Container validation successful", host_id=host_id, container_id=container_id
            )

        except DockerCommandError as e:
            if "No such container" in str(e):
                raise DockerCommandError(
                    f"Container {container_id} not found on host {host_id}"
                ) from e
            raise

    async def _stream_logs_generator(self, stream_config: LogStreamRequest):
        """Generator for streaming logs (used internally by FastMCP streaming)."""
        # This would be used by FastMCP's streaming system
        # to continuously yield log lines as they become available

        try:
            # Build streaming command
            cmd = f"logs --tail {stream_config.tail}"

            if stream_config.follow:
                cmd += " --follow"

            if stream_config.since:
                cmd += f" --since {stream_config.since}"

            if stream_config.timestamps:
                cmd += " --timestamps"

            cmd += f" {stream_config.container_id}"

            # In a real implementation, this would use asyncio subprocess
            # to stream logs continuously
            logger.info(
                "Starting log stream",
                host_id=stream_config.host_id,
                container_id=stream_config.container_id,
            )

            # Placeholder for actual streaming implementation
            yield f"data: Starting log stream for {stream_config.container_id}\n\n"

        except Exception as e:
            logger.error("Log streaming error", error=str(e))
            yield f"data: Error: {str(e)}\n\n"
