"""Docker-related MCP Resources.

This module provides Docker host information, container listings, and compose stack
information using the docker:// URI scheme.
"""

import json
from typing import Any

import structlog
from fastmcp.resources.resource import FunctionResource

logger = structlog.get_logger()


class DockerInfoResource(FunctionResource):
    """MCP Resource for Docker host information.
    
    URI Pattern: docker://{host_id}/info
    Provides comprehensive Docker host information including system info,
    version details, and configuration.
    """

    def __init__(self, context_manager, host_service):
        """Initialize the Docker info resource.
        
        Args:
            context_manager: DockerContextManager for Docker command execution
            host_service: HostService for host operations
        """
        self.context_manager = context_manager
        self.host_service = host_service

        super().__init__(
            fn=self._get_docker_info,
            uri="docker://{host_id}/info",
            name="Docker Host Information",
            title="Docker host system information and configuration",
            description="Provides comprehensive Docker host information including version, system info, and configuration details",
            mime_type="application/json",
            tags={"docker", "system", "info"},
        )

    async def _get_docker_info(self, host_id: str, **kwargs) -> dict[str, Any]:
        """Get Docker host information.
        
        Args:
            host_id: Docker host identifier
            **kwargs: Additional parameters (currently unused)
            
        Returns:
            Docker host information as a dictionary
        """
        try:
            logger.info("Fetching Docker info", host_id=host_id)

            # Get Docker system info and version
            info_result = await self.context_manager.execute_docker_command(host_id, "info --format json")
            version_result = await self.context_manager.execute_docker_command(host_id, "version --format json")

            # Parse results
            docker_info = {}
            docker_version = {}

            if isinstance(info_result, dict) and "output" in info_result:
                try:
                    docker_info = json.loads(info_result["output"])
                except json.JSONDecodeError:
                    docker_info = {"error": "Failed to parse info output"}
            elif isinstance(info_result, dict):
                docker_info = info_result

            if isinstance(version_result, dict) and "output" in version_result:
                try:
                    docker_version = json.loads(version_result["output"])
                except json.JSONDecodeError:
                    docker_version = {"error": "Failed to parse version output"}
            elif isinstance(version_result, dict):
                docker_version = version_result

            # Get host configuration from our host service
            host_config = {}
            try:
                hosts_data = await self.host_service.list_docker_hosts()
                if hosts_data.get("success") and "hosts" in hosts_data:
                    for host in hosts_data["hosts"]:
                        if host.get("host_id") == host_id:
                            host_config = host
                            break
            except Exception as e:
                logger.debug("Failed to get host config", host_id=host_id, error=str(e))
                host_config = {"error": "Failed to get host configuration"}

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

        except Exception as e:
            logger.error("Failed to get Docker info", host_id=host_id, error=str(e))
            return {
                "success": False,
                "error": f"Failed to get Docker info: {str(e)}",
                "host_id": host_id,
                "resource_uri": f"docker://{host_id}/info",
                "resource_type": "docker_info",
            }


class DockerContainersResource(FunctionResource):
    """MCP Resource for Docker container listings.
    
    URI Pattern: docker://{host_id}/containers
    Parameters supported:
    - all_containers: Include stopped containers (default: False)
    - limit: Maximum containers to return (default: 20)
    - offset: Pagination offset (default: 0)
    """

    def __init__(self, container_service):
        """Initialize the Docker containers resource.
        
        Args:
            container_service: ContainerService for container operations
        """
        self.container_service = container_service

        super().__init__(
            fn=self._get_containers,
            uri="docker://{host_id}/containers",
            name="Docker Container Listings",
            title="List of Docker containers on a host",
            description="Provides comprehensive container information including status, networks, volumes, and compose project details",
            mime_type="application/json",
            tags={"docker", "containers"},
        )

    async def _get_containers(self, host_id: str, **kwargs) -> dict[str, Any]:
        """Get Docker containers for a host.
        
        Args:
            host_id: Docker host identifier
            **kwargs: Additional parameters for filtering and pagination
            
        Returns:
            Container listing data as a dictionary
        """
        try:
            # Extract parameters with defaults
            all_containers = kwargs.get("all_containers", False)
            limit = kwargs.get("limit", 20)
            offset = kwargs.get("offset", 0)

            logger.info(
                "Fetching containers",
                host_id=host_id,
                all_containers=all_containers,
                limit=limit,
                offset=offset,
            )

            # Use the container service to get containers
            result = await self.container_service.list_containers(
                host_id=host_id,
                all_containers=all_containers,
                limit=limit,
                offset=offset
            )

            # Convert ToolResult to dict if needed
            if hasattr(result, 'structured_content'):
                containers_data = result.structured_content
            else:
                containers_data = result

            # Add resource metadata
            containers_data["resource_uri"] = f"docker://{host_id}/containers"
            containers_data["resource_type"] = "containers"
            containers_data["parameters"] = {
                "all_containers": all_containers,
                "limit": limit,
                "offset": offset,
            }

            logger.info(
                "Containers fetched successfully",
                host_id=host_id,
                total_containers=containers_data.get("pagination", {}).get("total", 0),
                returned=containers_data.get("pagination", {}).get("returned", 0),
            )

            return containers_data

        except Exception as e:
            logger.error("Failed to get containers", host_id=host_id, error=str(e))
            return {
                "success": False,
                "error": f"Failed to get containers: {str(e)}",
                "host_id": host_id,
                "resource_uri": f"docker://{host_id}/containers",
                "resource_type": "containers",
            }


class DockerComposeResource(FunctionResource):
    """MCP Resource for Docker Compose stack information.
    
    URI Pattern: docker://{host_id}/compose
    Provides information about Docker Compose stacks and projects on a host.
    """

    def __init__(self, stack_service):
        """Initialize the Docker Compose resource.
        
        Args:
            stack_service: StackService for stack operations
        """
        self.stack_service = stack_service

        super().__init__(
            fn=self._get_compose_info,
            uri="docker://{host_id}/compose",
            name="Docker Compose Information",
            title="Docker Compose stacks and projects",
            description="Provides information about Docker Compose stacks, projects, and their configurations on a host",
            mime_type="application/json",
            tags={"docker", "compose", "stacks"},
        )

    async def _get_compose_info(self, host_id: str, **kwargs) -> dict[str, Any]:
        """Get Docker Compose information for a host.
        
        Args:
            host_id: Docker host identifier
            **kwargs: Additional parameters (currently unused)
            
        Returns:
            Compose stack information as a dictionary
        """
        try:
            logger.info("Fetching compose info", host_id=host_id)

            # Get stacks information
            result = await self.stack_service.list_stacks(host_id)

            # Convert ToolResult to dict if needed
            if hasattr(result, 'structured_content'):
                compose_data = result.structured_content
            else:
                compose_data = result

            # Enhance with additional compose-specific information
            if compose_data.get("success"):
                # Group stacks by compose project
                projects = {}
                stacks = compose_data.get("stacks", [])

                for stack in stacks:
                    project_name = stack.get("project_name", "unknown")
                    if project_name not in projects:
                        projects[project_name] = {
                            "project_name": project_name,
                            "services": [],
                            "total_containers": 0,
                            "running_containers": 0,
                            "compose_file": stack.get("compose_file", ""),
                        }

                    projects[project_name]["services"].append(stack.get("service", ""))
                    projects[project_name]["total_containers"] += 1
                    if stack.get("status", "").lower() in ["running", "up"]:
                        projects[project_name]["running_containers"] += 1

                compose_data["compose_projects"] = list(projects.values())
                compose_data["total_projects"] = len(projects)

            # Add resource metadata
            compose_data["resource_uri"] = f"docker://{host_id}/compose"
            compose_data["resource_type"] = "compose"
            compose_data["host_id"] = host_id

            logger.info(
                "Compose info fetched successfully",
                host_id=host_id,
                total_stacks=compose_data.get("total_stacks", 0),
                total_projects=compose_data.get("total_projects", 0),
            )

            return compose_data

        except Exception as e:
            logger.error("Failed to get compose info", host_id=host_id, error=str(e))
            return {
                "success": False,
                "error": f"Failed to get compose info: {str(e)}",
                "host_id": host_id,
                "resource_uri": f"docker://{host_id}/compose",
                "resource_type": "compose",
            }
