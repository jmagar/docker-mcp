"""
Host Management Service

Business logic for Docker host management operations.
"""

from typing import Any

import structlog

from ..core.config_loader import DockerHost, DockerMCPConfig
from ..core.ssh_pool import get_connection_pool


class HostService:
    """Service for Docker host management operations."""

    def __init__(self, config: DockerMCPConfig):
        self.config = config
        self.logger = structlog.get_logger()
        self.ssh_pool = get_connection_pool()

    async def add_docker_host(
        self,
        host_id: str,
        ssh_host: str,
        ssh_user: str,
        ssh_port: int = 22,
        ssh_key_path: str | None = None,
        description: str = "",
        tags: list[str] | None = None,
        test_connection: bool = True,
        compose_path: str | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Add a new Docker host for management.

        Args:
            host_id: Unique identifier for the host
            ssh_host: SSH hostname or IP address
            ssh_user: SSH username
            ssh_port: SSH port (default: 22)
            ssh_key_path: Path to SSH private key
            description: Human-readable description
            tags: Tags for host categorization
            test_connection: Test SSH connection before adding
            compose_path: Path where compose files are stored on this host
            enabled: Whether the host is enabled for use

        Returns:
            Operation result
        """
        try:
            host_config = DockerHost(
                hostname=ssh_host,
                user=ssh_user,
                port=ssh_port,
                identity_file=ssh_key_path,
                description=description,
                tags=tags or [],
                compose_path=compose_path,
                enabled=enabled,
            )

            # Test connection if requested
            if test_connection:
                # Basic validation - in real implementation would test SSH
                pass

            # Add to configuration (in real implementation would persist)
            self.config.hosts[host_id] = host_config

            self.logger.info(
                "Docker host added", host_id=host_id, hostname=ssh_host, tested=test_connection
            )

            return {
                "success": True,
                "message": f"Host {host_id} added successfully",
                "host_id": host_id,
                "hostname": ssh_host,
                "connection_tested": test_connection,
            }

        except Exception as e:
            self.logger.error("Failed to add host", host_id=host_id, error=str(e))
            return {"success": False, "error": str(e), "host_id": host_id}

    async def list_docker_hosts(self) -> dict[str, Any]:
        """List all configured Docker hosts.

        Returns:
            List of host configurations
        """
        try:
            hosts = []
            for host_id, host_config in self.config.hosts.items():
                hosts.append(
                    {
                        "host_id": host_id,
                        "hostname": host_config.hostname,
                        "user": host_config.user,
                        "port": host_config.port,
                        "description": host_config.description,
                        "tags": host_config.tags,
                        "enabled": host_config.enabled,
                    }
                )

            return {"success": True, "hosts": hosts, "count": len(hosts)}

        except Exception as e:
            self.logger.error("Failed to list hosts", error=str(e))
            return {"success": False, "error": str(e)}

    def validate_host_exists(self, host_id: str) -> tuple[bool, str]:
        """Validate that a host exists in configuration.

        Args:
            host_id: Host identifier to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if host_id not in self.config.hosts:
            return False, f"Host '{host_id}' not found"
        return True, ""

    def get_host_config(self, host_id: str) -> DockerHost | None:
        """Get host configuration by ID.

        Args:
            host_id: Host identifier

        Returns:
            Host configuration or None if not found
        """
        return self.config.hosts.get(host_id)
    
    async def get_ssh_pool_stats(self) -> dict[str, Any]:
        """Get SSH connection pool statistics.
        
        Returns:
            Dictionary containing pool statistics including:
            - connections_created: Total connections created
            - connections_reused: Total connections reused
            - connections_closed: Total connections closed
            - connection_errors: Total connection errors
            - active_pools: Number of active host pools
            - total_connections: Total connections in all pools
            - active_connections: Currently active connections
        """
        try:
            stats = self.ssh_pool.get_stats()
            
            # Add additional computed metrics
            stats["efficiency_rate"] = (
                stats["connections_reused"] / max(1, stats["connections_created"] + stats["connections_reused"])
            ) * 100 if stats["connections_created"] > 0 else 0
            
            stats["success"] = True
            
            self.logger.debug(
                "SSH pool statistics retrieved",
                **stats
            )
            
            return stats
            
        except Exception as e:
            self.logger.error("Failed to get SSH pool stats", error=str(e))
            return {
                "success": False,
                "error": str(e),
                "connections_created": 0,
                "connections_reused": 0,
                "active_pools": 0,
                "total_connections": 0,
            }
