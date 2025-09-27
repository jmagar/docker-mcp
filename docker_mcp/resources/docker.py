"""Docker-related MCP Resources.

This module provides Docker host information, container listings, and compose stack
information using the docker:// URI scheme.
"""

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docker_mcp.core.docker_context import DockerContextManager
    from docker_mcp.services.container import ContainerService
    from docker_mcp.services.host import HostService
    from docker_mcp.services.stack_service import StackService

import docker
import structlog
from fastmcp.resources.resource import FunctionResource
from fastmcp.tools.tool import ToolResult
from pydantic import AnyUrl

from docker_mcp.core.error_response import DockerMCPErrorResponse

logger = structlog.get_logger()


# Helper functions for parameter coercion
def _coerce_bool(value: bool | str | None) -> bool:
    """Coerce various types to boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _coerce_int(
    value: int | str | None, default: int, min_val: int = 0, max_val: int | None = None
) -> int:
    """Coerce various types to integer with bounds."""
    if value is None:
        return default
    try:
        if isinstance(value, int):
            result = value
        else:
            result = int(str(value).strip())

        result = max(min_val, result)
        if max_val is not None:
            result = min(result, max_val)
        return result
    except (TypeError, ValueError):
        return default


def _coerce_log_lines(value: int | str | bool | None) -> int:
    """Coerce various types to log line count."""
    if isinstance(value, bool):
        return 100 if value else 0
    if isinstance(value, int):
        return max(0, min(value, 1000))
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"", "false", "0", "no", "off"}:
            return 0
        if text in {"true", "yes", "on"}:
            return 100
        try:
            parsed = int(text)
            return max(0, min(parsed, 1000))
        except ValueError:
            return 0
    return 0


def _extract_container_data(result: Any) -> dict[str, Any]:
    """Extract and validate container data from service result."""
    if isinstance(result, ToolResult):
        data = result.structured_content or {}
    elif isinstance(result, dict):
        data = result
    else:
        data = {}

    if not isinstance(data, dict):
        data = {"success": False, "error": "Unexpected container payload"}

    return data


async def _fetch_container_logs(
    container_service: Any, host_id: str, container_id: str, log_lines: int
) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch container logs if requested."""
    if log_lines <= 0 or not hasattr(container_service, "logs_service"):
        return None, None

    try:
        logs_result = await container_service.logs_service.get_container_logs(
            host_id=host_id,
            container_id=container_id,
            lines=log_lines,
            timestamps=False,
        )

        if isinstance(logs_result, dict) and logs_result.get("success"):
            log_data = logs_result.get("data") or {}
            if isinstance(log_data, dict):
                return {
                    "lines": log_data.get("logs", []),
                    "truncated": log_data.get("truncated", False),
                    "timestamp": log_data.get("timestamp"),
                }, None
            else:
                return None, "Unexpected logs payload"
        else:
            error_msg = (
                logs_result.get("error")
                if isinstance(logs_result, dict)
                else "Failed to retrieve logs"
            )
            return None, error_msg
    except Exception as log_exc:
        logger.error(
            "Failed to include container logs",
            host_id=host_id,
            container_id=container_id,
            error=str(log_exc),
        )
        return None, str(log_exc)


async def _fetch_container_stats(
    container_service: Any, host_id: str, container_id: str, include_stats: bool
) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch container stats if requested."""
    if not include_stats:
        return None, None

    try:
        stats_result = await container_service.container_tools.get_container_stats(
            host_id, container_id
        )

        if isinstance(stats_result, dict) and stats_result.get("success"):
            return stats_result.get("data") or {}, None
        else:
            error_msg = (
                stats_result.get("error")
                if isinstance(stats_result, dict)
                else "Failed to retrieve stats"
            )
            return None, error_msg
    except Exception as stats_exc:
        logger.error(
            "Failed to include container stats",
            host_id=host_id,
            container_id=container_id,
            error=str(stats_exc),
        )
        return None, str(stats_exc)


class DockerInfoResource(FunctionResource):
    """MCP Resource for Docker host information.

    URI Pattern: docker://{host_id}/info
    Provides comprehensive Docker host information including system info,
    version details, and configuration.
    """

    def __init__(self, context_manager: "DockerContextManager", host_service: "HostService"):
        """Initialize the Docker info resource.

        Args:
            context_manager: DockerContextManager for Docker command execution
            host_service: HostService for host operations
        """

        # Create the function with dependencies captured in closure
        async def _get_docker_info(host_id: str, **kwargs) -> dict[str, Any]:
            """Get Docker host information.

            Args:
                host_id: Docker host identifier
                **kwargs: Additional parameters (currently unused)

            Returns:
                Docker host information as a dictionary
            """
            try:
                logger.info("Fetching Docker info", host_id=host_id)

                # Get Docker client and retrieve info/version using Docker SDK
                client = await context_manager.get_client(host_id)
                if client is None:
                    logger.warning("No Docker client available", host_id=host_id)
                    error_response = DockerMCPErrorResponse.docker_context_error(
                        host_id=host_id,
                        operation="get_client",
                        cause="Docker client unavailable for host",
                    )
                    # Add resource-specific context
                    error_response.update(
                        {
                            "resource_uri": f"docker://{host_id}/info",
                            "resource_type": "docker_info",
                        }
                    )
                    return error_response

                # Get Docker system info and version using SDK
                docker_info = await asyncio.to_thread(client.info)
                docker_version = await asyncio.to_thread(client.version)

                # Get host configuration from our host service
                try:
                    host = host_service.get_host_config(host_id)
                    host_config = host.model_dump() if host else {"error": "Host not found"}
                except Exception as e:
                    logger.debug("Failed to get host config", host_id=host_id, error=str(e))
                    host_config = {"error": f"Failed to get host config: {str(e)}"}

                result = {
                    "success": True,
                    "host_id": host_id,
                    "docker_info": docker_info,
                    "docker_version": docker_version,
                    "host_config": host_config,
                    "resource_uri": f"docker://{host_id}/info",
                    "resource_type": "docker_info",
                }

                logger.info(
                    "Docker info fetched successfully",
                    host_id=host_id,
                    docker_version=docker_version.get("Server", {}).get("Version", "unknown"),
                )

                return result

            except docker.errors.APIError as e:
                logger.error("Docker API error getting info", host_id=host_id, error=str(e))
                return {
                    "success": False,
                    "error": f"Docker API error: {str(e)}",
                    "host_id": host_id,
                    "resource_uri": f"docker://{host_id}/info",
                }
            except Exception as e:
                logger.error("Failed to get Docker info", host_id=host_id, error=str(e))
                return {
                    "success": False,
                    "error": f"Failed to get Docker info: {str(e)}",
                    "host_id": host_id,
                    "resource_uri": f"docker://{host_id}/info",
                    "resource_type": "docker_info",
                }

        super().__init__(
            fn=_get_docker_info,
            uri=AnyUrl("docker://{host_id}/info"),
            name="Docker Host Information",
            title="Docker host system information and configuration",
            description="Provides comprehensive Docker host information including version, system info, and configuration details",
            mime_type="application/json",
            tags={"docker", "system", "info"},
        )


class StackListResource(FunctionResource):
    """List Docker Compose stacks available on a host.

    URI Pattern: stacks://{host_id}
    Returns a summary of compose projects discovered on the host including
    services, status, and timestamps. Data comes from the stack service so it
    reflects the same view exposed through tooling.
    """

    def __init__(self, stack_service: "StackService"):
        async def _list_stacks(host_id: str) -> dict[str, Any]:
            try:
                result = await stack_service.list_stacks(host_id)
                data: dict[str, Any] = {}

                if isinstance(result, ToolResult):
                    data = stack_service._unwrap(result)
                elif isinstance(result, dict):
                    data = result

                if not isinstance(data, dict):
                    data = {"success": False, "error": "Unexpected stacks payload"}

                stacks = data.get("stacks", [])
                summary = data.get("formatted_output")

                return {
                    "success": bool(data.get("success", False) and stacks is not None),
                    "host_id": host_id,
                    "resource_uri": f"stacks://{host_id}",
                    "resource_type": "stack_list",
                    "stacks": stacks,
                    "summary": summary,
                    "total_stacks": len(stacks) if isinstance(stacks, list) else 0,
                    "timestamp": data.get("timestamp"),
                }
            except Exception as exc:
                logger.error("Failed to list stacks", host_id=host_id, error=str(exc))
                return {
                    "success": False,
                    "error": f"Failed to list stacks: {exc}",
                    "host_id": host_id,
                    "resource_uri": f"stacks://{host_id}",
                    "resource_type": "stack_list",
                }

        super().__init__(
            fn=_list_stacks,
            uri=AnyUrl("stacks://{host_id}"),
            name="Compose Stacks",
            title="Docker Compose stacks available on a host",
            description="Lists compose stacks detected on the specified host including services and current status.",
            mime_type="application/json",
            tags={"docker", "compose", "stacks"},
        )


class StackDetailsResource(FunctionResource):
    """Return docker-compose content for a specific stack.

    URI Pattern: stacks://{host_id}/{stack_name}
    """

    def __init__(self, stack_service: "StackService"):
        async def _stack_details(host_id: str, stack_name: str) -> dict[str, Any]:
            try:
                result = await stack_service.get_stack_compose_file(host_id, stack_name)

                if isinstance(result, ToolResult):
                    data = result.structured_content or {}
                elif isinstance(result, dict):
                    data = result
                else:
                    data = {}

                if not isinstance(data, dict):
                    data = {"success": False, "error": "Unexpected stack detail payload"}

                compose_content = data.get("compose_content", "")

                return {
                    "success": bool(data.get("success", False) and compose_content),
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "resource_uri": f"stacks://{host_id}/{stack_name}",
                    "resource_type": "stack_details",
                    "compose_content": compose_content,
                    "timestamp": data.get("timestamp"),
                    "error": data.get("error"),
                }
            except Exception as exc:
                logger.error(
                    "Failed to fetch compose content",
                    host_id=host_id,
                    stack_name=stack_name,
                    error=str(exc),
                )
                return {
                    "success": False,
                    "error": f"Failed to fetch compose content: {exc}",
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "resource_uri": f"stacks://{host_id}/{stack_name}",
                    "resource_type": "stack_details",
                }

        super().__init__(
            fn=_stack_details,
            uri=AnyUrl("stacks://{host_id}/{stack_name}"),
            name="Compose Stack Details",
            title="Docker Compose definition for a stack",
            description="Returns the docker-compose specification currently deployed for the requested stack.",
            mime_type="application/json",
            tags={"docker", "compose", "stacks"},
        )


class ContainerListResource(FunctionResource):
    """List containers running on a host.

    URI Pattern: containers://{host_id}
    Optional query parameters:
      - all (bool): include stopped containers.
      - limit (int) / offset (int): pagination controls.
    """

    def __init__(self, container_service: "ContainerService"):
        async def _list_containers(
            host_id: str,
            *,
            all_containers: bool | str | None = None,
            limit: int | str | None = None,
            offset: int | str | None = None,
        ) -> dict[str, Any]:
            try:
                # Use helper functions for parameter coercion
                include_all = _coerce_bool(all_containers)
                limit_value = _coerce_int(limit, default=20, min_val=1, max_val=1000)
                offset_value = _coerce_int(offset, default=0, min_val=0)

                # Call the service
                result = await container_service.list_containers(
                    host_id,
                    all_containers=include_all,
                    limit=limit_value,
                    offset=offset_value,
                )

                # Extract and validate data
                data = _extract_container_data(result)
                containers = data.get("containers", [])
                pagination = data.get("pagination", {})

                # Build response
                return {
                    "success": bool(data.get("success", False)),
                    "host_id": host_id,
                    "resource_uri": f"containers://{host_id}",
                    "resource_type": "container_list",
                    "containers": containers,
                    "pagination": pagination,
                    "summary": data.get("formatted_output"),
                    "parameters": {
                        "all": include_all,
                        "limit": limit_value,
                        "offset": offset_value,
                    },
                }
            except Exception as exc:
                logger.error("Failed to list containers", host_id=host_id, error=str(exc))
                return {
                    "success": False,
                    "error": f"Failed to list containers: {exc}",
                    "host_id": host_id,
                    "resource_uri": f"containers://{host_id}",
                    "resource_type": "container_list",
                }

        super().__init__(
            fn=_list_containers,
            uri=AnyUrl("containers://{host_id}"),
            name="Containers",
            title="Docker containers present on a host",
            description="Lists Docker containers for the specified host with optional pagination and filtering.",
            mime_type="application/json",
            tags={"docker", "containers"},
        )


class ContainerDetailsResource(FunctionResource):
    """Detailed inspection of a specific container.

    URI Pattern: containers://{host_id}/{container_id}
    """

    def __init__(self, container_service: "ContainerService"):
        async def _container_details(
            host_id: str,
            container_id: str,
            *,
            logs: int | str | bool | None = None,
            stats: bool | str | None = None,
        ) -> dict[str, Any]:
            try:
                # Get container info
                result = await container_service.get_container_info(host_id, container_id)
                data = _extract_container_data(result)
                info = data.get("info") or data.get("data") or {}

                # Coerce parameters
                log_lines = _coerce_log_lines(logs)
                include_stats = _coerce_bool(stats)

                # Fetch logs if requested
                logs_payload, logs_error = await _fetch_container_logs(
                    container_service, host_id, container_id, log_lines
                )

                # Fetch stats if requested
                stats_payload, stats_error = await _fetch_container_stats(
                    container_service, host_id, container_id, include_stats
                )

                # Build base response
                response: dict[str, Any] = {
                    "success": bool(data.get("success", False)),
                    "host_id": host_id,
                    "container_id": container_id,
                    "resource_uri": f"containers://{host_id}/{container_id}",
                    "resource_type": "container_details",
                    "info": info,
                    "summary": data.get("formatted_output"),
                    "timestamp": data.get("timestamp"),
                    "error": data.get("error"),
                }

                # Add optional fields
                if logs_payload is not None:
                    response["logs"] = logs_payload
                if logs_error:
                    response["logs_error"] = logs_error
                if stats_payload is not None:
                    response["stats"] = stats_payload
                if stats_error:
                    response["stats_error"] = stats_error

                return response

            except Exception as exc:
                logger.error(
                    "Failed to inspect container",
                    host_id=host_id,
                    container_id=container_id,
                    error=str(exc),
                )
                return {
                    "success": False,
                    "error": f"Failed to inspect container: {exc}",
                    "host_id": host_id,
                    "container_id": container_id,
                    "resource_uri": f"containers://{host_id}/{container_id}",
                    "resource_type": "container_details",
                }

        super().__init__(
            fn=_container_details,
            uri=AnyUrl("containers://{host_id}/{container_id}"),
            name="Container Details",
            title="Detailed information for a Docker container",
            description="Inspect a specific container including configuration, state, networks, and volume bindings.",
            mime_type="application/json",
            tags={"docker", "containers"},
        )
