"""Port mapping MCP Resource.

This resource provides port mapping information using the ports:// URI scheme.
It serves as a clean, cacheable alternative to the ports action in the docker_hosts tool.
"""

import ipaddress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docker_mcp.server import DockerMCPServer
    from docker_mcp.services.container import ContainerService

import structlog
from fastmcp.resources.resource import FunctionResource
from pydantic import AnyUrl

from ..models.enums import ProtocolLiteral

logger = structlog.get_logger()


def _validate_and_normalize_protocol(protocol: str | None) -> ProtocolLiteral | None:
    """Validate and normalize protocol string against ProtocolLiteral type.

    Args:
        protocol: Protocol string to validate (case-insensitive)

    Returns:
        Normalized protocol value or None if invalid/None

    Raises:
        ValueError: If protocol is invalid
    """
    if protocol is None:
        return None

    protocol_lower = protocol.lower().strip()
    allowed_protocols = get_args(ProtocolLiteral)
    if protocol_lower not in allowed_protocols:
        raise ValueError(
            f"Invalid protocol '{protocol}'. Must be one of: {', '.join(allowed_protocols)}"
        )

    return cast(ProtocolLiteral, protocol_lower)  # type: ignore[return-value]


def _validate_host_ip(host_ip: str | None) -> str:
    """Validate HostIp field from Docker port binding.

    Args:
        host_ip: Host IP address to validate

    Returns:
        Validated host IP address

    Raises:
        ValueError: If host IP is invalid
    """
    if host_ip in (None, ""):
        # None or empty string means all interfaces (equivalent to 0.0.0.0)
        return "0.0.0.0"
    
    if host_ip == "0.0.0.0":
        # Valid all-interfaces binding
        return host_ip
    
    try:
        # Validate as IP address (IPv4 or IPv6)
        ip_obj = ipaddress.ip_address(host_ip)
        return str(ip_obj)
    except ValueError as e:
        raise ValueError(f"Invalid IP address '{host_ip}': {str(e)}") from e


def _validate_host_port(host_port: str | None) -> int:
    """Validate HostPort field from Docker port binding.

    Args:
        host_port: Host port to validate

    Returns:
        Validated port number as integer

    Raises:
        ValueError: If host port is invalid
    """
    if host_port is None:
        raise ValueError("HostPort cannot be None")
    
    if host_port == "":
        raise ValueError("HostPort cannot be empty")
    
    try:
        port_int = int(host_port)
    except ValueError as e:
        raise ValueError(f"HostPort must be numeric, got '{host_port}'") from e
    
    if not (1 <= port_int <= 65535):
        raise ValueError(f"HostPort must be between 1 and 65535, got {port_int}")
    
    return port_int


def _validate_port_binding(bind: dict[str, Any] | None) -> dict[str, Any]:
    """Validate port binding fields for safety.

    Args:
        bind: Port binding dictionary from Docker API

    Returns:
        Validated binding dictionary with normalized values

    Raises:
        ValueError: If binding data is invalid
    """
    if bind is None:
        raise ValueError("Port binding cannot be None")
    
    if not isinstance(bind, dict):
        raise ValueError(f"Port binding must be a dictionary, got {type(bind)}")
    
    # Validate HostIp if present
    host_ip = bind.get("HostIp")
    try:
        validated_ip = _validate_host_ip(host_ip)
    except ValueError as e:
        raise ValueError(f"Invalid HostIp in port binding: {str(e)}") from e
    
    # Validate HostPort if present
    host_port = bind.get("HostPort")
    try:
        validated_port = _validate_host_port(host_port)
    except ValueError as e:
        raise ValueError(f"Invalid HostPort in port binding: {str(e)}") from e
    
    # Return validated binding
    return {
        "HostIp": validated_ip,
        "HostPort": str(validated_port),
        **{k: v for k, v in bind.items() if k not in ("HostIp", "HostPort")}
    }


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

    def __init__(self, _container_service: "ContainerService", server_instance: "DockerMCPServer"):
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

                # Validate and normalize protocol parameter
                try:
                    normalized_protocol = _validate_and_normalize_protocol(filter_protocol)
                    filter_protocol = normalized_protocol
                except ValueError as e:
                    logger.error(
                        "Invalid protocol parameter",
                        host_id=host_id,
                        filter_protocol=filter_protocol,
                        error=str(e),
                    )
                    return {
                        "success": False,
                        "error": str(e),
                        "host_id": host_id,
                        "resource_uri": f"ports://{host_id}",
                        "resource_type": "port_mappings",
                    }

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

                if (
                    filter_project
                    or filter_range
                    or filter_protocol
                    or scan_available
                    or suggest_next
                ):
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
        super().__init__(
            fn=_get_port_data,
            uri=AnyUrl("ports://{host_id}"),
            name="Docker Port Mappings",
            title="Port mappings for Docker hosts",
            description="Provides comprehensive port mapping information for Docker containers on a host",
            mime_type="application/json",
            tags={"docker", "ports", "networking"},
        )
