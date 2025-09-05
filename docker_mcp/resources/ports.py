"""Port mapping MCP Resource.

This resource provides port mapping information using the ports:// URI scheme.
It serves as a clean, cacheable alternative to the ports action in the docker_hosts tool.
"""

from typing import Any

import structlog
from fastmcp.resources.resource import FunctionResource

logger = structlog.get_logger()


class PortMappingResource(FunctionResource):
    """MCP Resource for port mapping data.

    URI Pattern: ports://{host_id}
    Parameters supported:
    - include_stopped: Include stopped containers (default: False)
    - export_format: Export format (json, csv, markdown)
    - filter_project: Filter by compose project
    - filter_range: Filter by port range (e.g., '8000-9000')
    - filter_protocol: Filter by protocol (TCP, UDP)
    - scan_available: Scan for available ports (default: False)
    - suggest_next: Suggest next available port (default: False)
    - use_cache: Use cached data (default: True)
    """

    def __init__(self, container_service, server_instance):
        """Initialize the port mapping resource.

        Dependencies are captured in a closure to avoid setting attributes
        that Pydantic's BaseModel would reject.
        """

        async def _get_port_data(host_id: str, **kwargs) -> dict[str, Any]:
            try:
                include_stopped = kwargs.get("include_stopped", False)
                export_format = kwargs.get("export_format")
                filter_project = kwargs.get("filter_project")
                filter_range = kwargs.get("filter_range")
                filter_protocol = kwargs.get("filter_protocol")
                scan_available = kwargs.get("scan_available", False)
                suggest_next = kwargs.get("suggest_next", False)
                use_cache = kwargs.get("use_cache", True)

                logger.info(
                    "Fetching port data",
                    host_id=host_id,
                    include_stopped=include_stopped,
                    export_format=export_format,
                    use_cache=use_cache,
                )

                # Use the existing port listing functionality (ToolResult)
                result = await server_instance.list_host_ports(
                    host_id=host_id,
                    include_stopped=include_stopped,
                )

                if hasattr(result, "structured_content") and result.structured_content is not None:
                    data = result.structured_content
                elif isinstance(result, dict):
                    data = result
                else:
                    data = {"success": False, "error": "Unexpected response type"}

                if filter_project or filter_range or filter_protocol or scan_available or suggest_next:
                    if isinstance(data, dict) and data.get("success"):
                        data["warning"] = "Advanced filtering parameters not yet implemented"

                # Add resource metadata
                data["resource_uri"] = f"ports://{host_id}"
                data["resource_type"] = "port_mappings"
                data["parameters"] = {
                    "include_stopped": include_stopped,
                    "export_format": export_format,
                    "filter_project": filter_project,
                    "filter_range": filter_range,
                    "filter_protocol": filter_protocol,
                    "scan_available": scan_available,
                    "suggest_next": suggest_next,
                    "use_cache": use_cache,
                }

                logger.info(
                    "Port data fetched successfully",
                    host_id=host_id,
                    total_ports=data.get("total_ports", 0),
                    success=data.get("success", False),
                )

                return data

            except Exception as e:
                logger.error(
                    "Failed to get port data",
                    host_id=host_id,
                    error=str(e),
                )
                return {
                    "success": False,
                    "error": f"Failed to get port data: {str(e)}",
                    "host_id": host_id,
                    "resource_uri": f"ports://{host_id}",
                    "resource_type": "port_mappings",
                }

        # Initialize FunctionResource with closure-based function
        from pydantic import AnyUrl

        super().__init__(
            fn=_get_port_data,
            uri=AnyUrl("ports://{host_id}"),
            name="Docker Port Mappings",
            title="Port mappings for Docker hosts",
            description="Provides comprehensive port mapping information for Docker containers on a host",
            mime_type="application/json",
            tags={"docker", "ports", "networking"},
        )
