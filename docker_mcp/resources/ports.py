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
        
        Args:
            container_service: ContainerService instance for port operations
            server_instance: Server instance for enhanced port functionality
        """
        self.container_service = container_service
        self.server_instance = server_instance

        # Call parent constructor with our port data function
        super().__init__(
            fn=self._get_port_data,
            uri="ports://{host_id}",
            name="Docker Port Mappings",
            title="Port mappings for Docker hosts",
            description="Provides comprehensive port mapping information for Docker containers on a host",
            mime_type="application/json",
            tags={"docker", "ports", "networking"},
        )

    async def _get_port_data(self, host_id: str, **kwargs) -> dict[str, Any]:
        """Get port mapping data for a host.
        
        Args:
            host_id: Docker host identifier
            **kwargs: Additional parameters for filtering and enhancement
            
        Returns:
            Port mapping data as a dictionary
        """
        try:
            # Extract parameters with defaults
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

            # Use the existing port listing functionality
            result = await self.server_instance.list_host_ports(
                host_id=host_id,
                include_stopped=include_stopped,
            )
            
            # Convert ToolResult to dict if needed
            if hasattr(result, 'content'):
                # Handle ToolResult format
                if result.content and len(result.content) > 0:
                    if hasattr(result.content[0], 'text'):
                        # Extract the actual data from the ToolResult
                        import json
                        try:
                            result = json.loads(result.content[0].text)
                        except (json.JSONDecodeError, AttributeError):
                            result = {"success": False, "error": "Failed to parse port data"}
                    else:
                        result = {"success": False, "error": "No content in response"}
                else:
                    result = {"success": False, "error": "Empty response"}
            
            # TODO: Implement additional filtering parameters in future versions
            # Currently only basic host_id and include_stopped are supported
            if filter_project or filter_range or filter_protocol or scan_available or suggest_next:
                if isinstance(result, dict) and result.get("success"):
                    result["warning"] = "Advanced filtering parameters not yet implemented"

            # Add resource metadata
            result["resource_uri"] = f"ports://{host_id}"
            result["resource_type"] = "port_mappings"
            result["parameters"] = {
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
                total_ports=result.get("total_ports", 0),
                success=result.get("success", False),
            )

            return result

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

    async def read(self) -> str | bytes:
        """Read the resource content as JSON string.
        
        This method is called when the resource is accessed by MCP clients.
        Since this is a FunctionResource, the actual data loading is deferred
        until this method is called.
        """
        # For FunctionResource, we need to call the wrapped function
        # This will be handled by the parent class automatically
        return await super().read()
