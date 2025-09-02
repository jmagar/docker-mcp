"""
Logs Service

Thin service wrapper around LogTools to provide logs functionality from the
service layer, enabling consistent dependency injection and future refactors.
"""

from typing import Any

import structlog

from ..core.config_loader import DockerMCPConfig
from ..core.docker_context import DockerContextManager
from ..tools.logs import LogTools


class LogsService:
    """Service layer for container and compose logs operations."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self._tools = LogTools(config, context_manager)
        self.logger = structlog.get_logger()

    async def get_container_logs(
        self,
        host_id: str,
        container_id: str,
        lines: int = 100,
        since: str | None = None,
        timestamps: bool = False,
    ) -> dict[str, Any]:
        """Fetch recent container logs using underlying tools implementation."""
        return await self._tools.get_container_logs(
            host_id=host_id,
            container_id=container_id,
            lines=lines,
            since=since,
            timestamps=timestamps,
        )

    async def get_service_logs(
        self,
        host_id: str,
        service_name: str,
        lines: int = 100,
        since: str | None = None,
        timestamps: bool = False,
    ) -> dict[str, Any]:
        """Fetch recent Docker Compose service logs using underlying tools implementation."""
        return await self._tools.get_service_logs(
            host_id=host_id,
            service_name=service_name,
            lines=lines,
            since=since,
            timestamps=timestamps,
        )
