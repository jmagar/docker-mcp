"""
Host Management Service

Business logic for Docker host management operations.
"""

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any

import structlog

from ..constants import APPDATA_PATH, COMPOSE_PATH, DOCKER_COMPOSE_WORKING_DIR, HOST_ID
from ..core.config_loader import DockerHost, DockerMCPConfig, save_config
from ..utils import build_ssh_command


class HostService:
    """Service for Docker host management operations."""

    def __init__(self, config: DockerMCPConfig, cache_manager=None):
        self.config = config
        self.cache_manager = cache_manager
        self.logger = structlog.get_logger()

    def set_cache_manager(self, cache_manager):
        """Set the cache manager after initialization."""
        self.cache_manager = cache_manager

    async def add_docker_host(
        self,
        host_id: str,
        ssh_host: str,
        ssh_user: str,
        ssh_port: int = 22,
        ssh_key_path: str | None = None,
        description: str = "",
        tags: list[str] | None = None,
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

            # Always test connection (auto-enabled)
            connection_tested = await self._test_ssh_connection(
                ssh_host, ssh_user, ssh_port, ssh_key_path
            )

            if not connection_tested:
                return {
                    "success": False,
                    "error": f"SSH connection test failed for {ssh_user}@{ssh_host}:{ssh_port}",
                    HOST_ID: host_id,
                    "hostname": ssh_host,
                    "connection_tested": False,
                }

            # Add to configuration
            self.config.hosts[host_id] = host_config
            # Always save configuration changes to disk
            save_config(self.config, getattr(self.config, "config_file", None))

            self.logger.info(
                "Docker host added", host_id=host_id, hostname=ssh_host, tested=connection_tested
            )

            return {
                "success": True,
                "message": f"Host {host_id} added successfully (SSH connection verified)",
                HOST_ID: host_id,
                "hostname": ssh_host,
                "connection_tested": connection_tested,
            }

        except Exception as e:
            self.logger.error("Failed to add host", host_id=host_id, error=str(e))
            return {"success": False, "error": str(e), HOST_ID: host_id}

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
                        HOST_ID: host_id,
                        "hostname": host_config.hostname,
                        "user": host_config.user,
                        "port": host_config.port,
                        "description": host_config.description,
                        "tags": host_config.tags,
                        "enabled": host_config.enabled,
                        COMPOSE_PATH: host_config.compose_path,
                        APPDATA_PATH: host_config.appdata_path,
                        "zfs_capable": host_config.zfs_capable,
                        "zfs_dataset": host_config.zfs_dataset,
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

    async def edit_docker_host(
        self,
        host_id: str,
        ssh_host: str | None = None,
        ssh_user: str | None = None,
        ssh_port: int | None = None,
        ssh_key_path: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        compose_path: str | None = None,
        appdata_path: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Edit an existing Docker host configuration.

        Args:
            host_id: Unique identifier for the host
            ssh_host: SSH hostname or IP address (optional update)
            ssh_user: SSH username (optional update)
            ssh_port: SSH port (optional update)
            ssh_key_path: Path to SSH private key (optional update)
            description: Human-readable description (optional update)
            tags: Tags for host categorization (optional update)
            compose_path: Path where compose files are stored (optional update)
            appdata_path: Path where container data is stored (optional update)
            enabled: Whether the host is enabled (optional update)

        Returns:
            Operation result
        """
        try:
            # Check if host exists
            if host_id not in self.config.hosts:
                return {"success": False, "error": f"Host '{host_id}' not found", HOST_ID: host_id}

            # Get current configuration
            current_host = self.config.hosts[host_id]

            # Update only provided values
            updated_config = {
                "hostname": ssh_host if ssh_host is not None else current_host.hostname,
                "user": ssh_user if ssh_user is not None else current_host.user,
                "port": ssh_port if ssh_port is not None else current_host.port,
                "identity_file": ssh_key_path
                if ssh_key_path is not None
                else current_host.identity_file,
                "description": description if description is not None else current_host.description,
                "tags": tags if tags is not None else current_host.tags,
                COMPOSE_PATH: compose_path
                if compose_path is not None
                else current_host.compose_path,
                APPDATA_PATH: appdata_path
                if appdata_path is not None
                else current_host.appdata_path,
                "zfs_capable": current_host.zfs_capable,
                "zfs_dataset": current_host.zfs_dataset,
                "enabled": enabled if enabled is not None else current_host.enabled,
            }

            # Update the host configuration
            self.config.hosts[host_id] = DockerHost(**updated_config)
            # Always save configuration changes to disk
            save_config(self.config, getattr(self.config, "config_file", None))

            self.logger.info("Docker host updated", host_id=host_id)

            return {
                "success": True,
                "message": f"Host {host_id} updated successfully",
                HOST_ID: host_id,
                "updated_fields": [
                    k
                    for k, v in locals().items()
                    if k.startswith(
                        ("ssh_", "description", "tags", "compose_", "appdata_", "enabled")
                    )
                    and v is not None
                ],
            }

        except Exception as e:
            self.logger.error("Failed to edit host", host_id=host_id, error=str(e))
            return {"success": False, "error": str(e), HOST_ID: host_id}

    async def remove_docker_host(self, host_id: str) -> dict[str, Any]:
        """Remove a Docker host from configuration.

        Args:
            host_id: Unique identifier for the host to remove

        Returns:
            Operation result
        """
        try:
            # Check if host exists
            if host_id not in self.config.hosts:
                return {"success": False, "error": f"Host '{host_id}' not found", HOST_ID: host_id}

            # Store host info for response
            hostname = self.config.hosts[host_id].hostname

            # Remove the host
            del self.config.hosts[host_id]

            # Always save configuration changes to disk
            save_config(self.config, getattr(self.config, "config_file", None))

            self.logger.info("Docker host removed", host_id=host_id, hostname=hostname)

            return {
                "success": True,
                "message": f"Host {host_id} ({hostname}) removed successfully",
                HOST_ID: host_id,
                "hostname": hostname,
            }

        except Exception as e:
            self.logger.error("Failed to remove host", host_id=host_id, error=str(e))
            return {"success": False, "error": str(e), HOST_ID: host_id}

    async def test_connection(self, host_id: str) -> dict[str, Any]:
        """Test SSH connection to a Docker host.

        Args:
            host_id: Host identifier to test connection for

        Returns:
            Connection test result
        """
        try:
            # Check if host exists
            if host_id not in self.config.hosts:
                return {"success": False, "error": f"Host '{host_id}' not found", HOST_ID: host_id}

            host = self.config.hosts[host_id]

            # Build SSH command for connection test
            ssh_cmd = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "StrictHostKeyChecking=accept-new",
            ]

            if host.port != 22:
                ssh_cmd.extend(["-p", str(host.port)])

            if host.identity_file:
                ssh_cmd.extend(["-i", host.identity_file])

            ssh_cmd.append(f"{host.user}@{host.hostname}")
            ssh_cmd.append(
                "echo 'connection_test_ok' && docker version --format '{{.Server.Version}}' 2>/dev/null || echo 'docker_not_available'"
            )

            # Execute SSH test
            process = await asyncio.create_subprocess_exec(
                *ssh_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()
            output = stdout.decode().strip()
            error_output = stderr.decode().strip()

            if process.returncode == 0 and "connection_test_ok" in output:
                # Check if Docker is available
                docker_available = "docker_not_available" not in output
                docker_version = None

                if docker_available:
                    # Extract Docker version if available
                    lines = output.split("\n")
                    for line in lines:
                        if line and line != "connection_test_ok" and line != "docker_not_available":
                            docker_version = line.strip()
                            break

                return {
                    "success": True,
                    "message": "SSH connection successful",
                    HOST_ID: host_id,
                    "hostname": host.hostname,
                    "port": host.port,
                    "docker_available": docker_available,
                    "docker_version": docker_version,
                }
            else:
                error_msg = error_output if error_output else "SSH connection failed"
                return {
                    "success": False,
                    "error": f"SSH connection failed: {error_msg}",
                    HOST_ID: host_id,
                    "hostname": host.hostname,
                    "port": host.port,
                }

        except Exception as e:
            return {
                "success": False,
                "error": f"Connection test failed: {str(e)}",
                HOST_ID: host_id,
            }

    async def discover_host_capabilities(self, host_id: str) -> dict[str, Any]:
        """Discover host capabilities including paths and ZFS support.

        Args:
            host_id: Host identifier to discover capabilities for

        Returns:
            Discovery results with recommendations
        """
        try:
            # Check if host exists
            if host_id not in self.config.hosts:
                return {"success": False, "error": f"Host '{host_id}' not found", HOST_ID: host_id}

            host = self.config.hosts[host_id]

            # Run discovery operations in parallel
            results = await asyncio.gather(
                self._discover_compose_paths(host),
                self._discover_appdata_paths(host),
                self._discover_zfs_capability(host),
                return_exceptions=True,
            )

            compose_result = (
                results[0]
                if not isinstance(results[0], Exception)
                else {"paths": [], "recommended": None}
            )
            appdata_result = (
                results[1]
                if not isinstance(results[1], Exception)
                else {"paths": [], "recommended": None}
            )
            zfs_result = results[2] if not isinstance(results[2], Exception) else {"capable": False}

            # Compile results
            capabilities = {
                "success": True,
                HOST_ID: host_id,
                "compose_discovery": compose_result,
                "appdata_discovery": appdata_result,
                "zfs_discovery": zfs_result,
                "recommendations": [],
            }

            # Generate recommendations
            if compose_result["recommended"]:
                capabilities["recommendations"].append(
                    {
                        "type": COMPOSE_PATH,
                        "message": f"Set compose_path to '{compose_result['recommended']}'",
                        "value": compose_result["recommended"],
                    }
                )

            if appdata_result["recommended"]:
                capabilities["recommendations"].append(
                    {
                        "type": APPDATA_PATH,
                        "message": f"Set appdata_path to '{appdata_result['recommended']}'",
                        "value": appdata_result["recommended"],
                    }
                )

            if zfs_result["capable"]:
                # Auto-add 'zfs' tag to host if not already present
                host = self.config.hosts[host_id]
                if "zfs" not in host.tags:
                    host.tags.append("zfs")
                    host.zfs_capable = True
                    if zfs_result.get("dataset"):
                        host.zfs_dataset = zfs_result.get("dataset")
                    self.logger.info("Auto-added 'zfs' tag to host", host_id=host_id)

                    # Save configuration after adding tag
                    try:
                        save_config(self.config)
                        tag_added = True
                    except Exception as e:
                        self.logger.error(
                            "Failed to save config after adding zfs tag",
                            host_id=host_id,
                            error=str(e),
                        )
                        tag_added = False
                else:
                    tag_added = False  # Tag already existed

                capabilities["recommendations"].append(
                    {
                        "type": "zfs_config",
                        "message": "ZFS support detected and 'zfs' tag automatically added"
                        if tag_added
                        else "ZFS support detected ('zfs' tag already present)",
                        "zfs_dataset": zfs_result.get("dataset"),
                        "tag_added": tag_added,
                    }
                )

            self.logger.info(
                "Host capabilities discovered",
                host_id=host_id,
                compose_paths_found=len(compose_result["paths"]),
                appdata_paths_found=len(appdata_result["paths"]),
                zfs_capable=zfs_result["capable"],
            )

            return capabilities

        except Exception as e:
            self.logger.error("Failed to discover host capabilities", host_id=host_id, error=str(e))
            return {
                "success": False,
                "error": f"Discovery failed: {str(e)}",
                HOST_ID: host_id,
            }

    async def discover_all_hosts(self) -> dict[str, Any]:
        """Discover capabilities for all configured hosts.

        Returns:
            Discovery results for all hosts with summary
        """
        try:
            discovery_tasks = []
            host_ids = []

            # Create discovery tasks for all enabled hosts
            for host_id, host_config in self.config.hosts.items():
                if host_config.enabled:
                    discovery_tasks.append(self.discover_host_capabilities(host_id))
                    host_ids.append(host_id)

            if not host_ids:
                return {
                    "success": True,
                    "action": "discover_all",
                    "total_hosts": 0,
                    "message": "No enabled hosts to discover",
                    "discoveries": {},
                }

            self.logger.info(
                "Starting discovery for all hosts", total_hosts=len(host_ids), host_ids=host_ids
            )

            # Run all discoveries in parallel
            results = await asyncio.gather(*discovery_tasks, return_exceptions=True)

            # Compile results
            discoveries = {}
            successful_discoveries = 0
            failed_discoveries = 0

            for host_id, result in zip(host_ids, results, strict=False):
                if isinstance(result, Exception):
                    discoveries[host_id] = {
                        "success": False,
                        "error": str(result),
                        HOST_ID: host_id,
                    }
                    failed_discoveries += 1
                    self.logger.error("Host discovery failed", host_id=host_id, error=str(result))
                else:
                    discoveries[host_id] = result
                    if result.get("success", False):
                        successful_discoveries += 1
                    else:
                        failed_discoveries += 1

            self.logger.info(
                "Discovery completed for all hosts",
                total_hosts=len(host_ids),
                successful=successful_discoveries,
                failed=failed_discoveries,
            )

            return {
                "success": True,
                "action": "discover_all",
                "total_hosts": len(host_ids),
                "successful_discoveries": successful_discoveries,
                "failed_discoveries": failed_discoveries,
                "discoveries": discoveries,
                "summary": f"Discovered {successful_discoveries}/{len(host_ids)} hosts successfully",
            }

        except Exception as e:
            self.logger.error("Failed to discover all hosts", error=str(e))
            return {
                "success": False,
                "error": f"Discovery failed: {str(e)}",
                "action": "discover_all",
            }

    async def _discover_compose_paths(self, host: DockerHost) -> dict[str, Any]:
        """Discover Docker Compose file locations from running containers."""
        try:
            # Use cache manager if available, otherwise fall back to SSH
            if self.cache_manager:
                return await self._discover_compose_paths_cached(host)
            else:
                return await self._discover_compose_paths_ssh(host)
        except Exception as e:
            self.logger.error("Compose path discovery failed", host_id=host.hostname, error=str(e))
            return {"paths": [], "recommended": None, "error": str(e)}

    async def _discover_compose_paths_cached(self, host: DockerHost) -> dict[str, Any]:
        """Discover compose paths using cache manager."""
        try:
            # Find host_id from hostname
            host_id = None
            for hid, hconfig in self.config.hosts.items():
                if hconfig.hostname == host.hostname:
                    host_id = hid
                    break

            if not host_id:
                self.logger.warning("Could not find host_id for hostname", hostname=host.hostname)
                return {"paths": [], "recommended": None}

            # Get containers from cache
            containers = await self.cache_manager.get_containers(host_id)

            # Extract compose working directories
            compose_dirs = []
            for container in containers:
                if container.compose_working_dir:
                    compose_dirs.append(container.compose_working_dir)

            if compose_dirs:
                # Count parent directories (where compose files typically live)
                path_counter = Counter()
                for compose_dir in compose_dirs:
                    parent_dir = str(Path(compose_dir).parent)
                    path_counter[parent_dir] += 1

                # Get unique paths and recommend the most common one
                unique_paths = list(path_counter.keys())
                recommended = path_counter.most_common(1)[0][0] if path_counter else None

                self.logger.info(
                    "Discovered compose paths from cache",
                    host_id=host_id,
                    paths_found=len(unique_paths),
                    containers_checked=len(containers),
                    recommended=recommended,
                )

                return {"paths": unique_paths, "recommended": recommended}

            # No compose directories found
            self.logger.debug("No compose working directories found in cache", host_id=host_id)
            return {"paths": [], "recommended": None}

        except Exception as e:
            self.logger.error("Cache-based compose discovery failed", error=str(e))
            # Fall back to SSH method
            return await self._discover_compose_paths_ssh(host)

    async def _discover_compose_paths_ssh(self, host: DockerHost) -> dict[str, Any]:
        """Discover compose paths using SSH (fallback method)."""
        try:
            ssh_cmd = build_ssh_command(host)

            # Get compose working directories from all containers with compose labels
            inspect_cmd = ssh_cmd + [
                f"docker ps -aq --no-trunc | xargs -r docker inspect --format '{{{{index .Config.Labels \"{DOCKER_COMPOSE_WORKING_DIR}\"}}}}' 2>/dev/null | grep -v '^$' | sort | uniq"
            ]

            process = await asyncio.create_subprocess_exec(
                *inspect_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, _ = await process.communicate()

            if process.returncode == 0 and stdout:
                # Extract unique compose directories
                compose_dirs = [d.strip() for d in stdout.decode().strip().split("\n") if d.strip()]

                if compose_dirs:
                    # Find common base path by counting occurrences
                    path_counts = {}
                    for compose_dir in compose_dirs:
                        # Get parent directory (where compose files typically live)
                        parent_dir = str(Path(compose_dir).parent)
                        path_counts[parent_dir] = path_counts.get(parent_dir, 0) + 1

                    # Recommend the path with most compose projects
                    recommended = (
                        max(path_counts.items(), key=lambda x: x[1])[0] if path_counts else None
                    )

                    return {"paths": list(path_counts.keys()), "recommended": recommended}

            # Fallback to file system search if no running containers with compose labels
            fallback_cmd = ssh_cmd + [
                "find /opt /srv /home -maxdepth 3 -name 'docker-compose.*' -o -name 'compose.*' 2>/dev/null | head -10"
            ]

            process = await asyncio.create_subprocess_exec(
                *fallback_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, _ = await process.communicate()

            if process.returncode == 0 and stdout:
                compose_files = stdout.decode().strip().split("\n")
                directories = list(set(str(Path(f).parent) for f in compose_files if f.strip()))
                recommended = self._recommend_compose_path(directories)
                return {"paths": directories, "recommended": recommended}

            return {"paths": [], "recommended": None}

        except Exception as e:
            self.logger.warning(
                "Failed to discover compose paths", hostname=host.hostname, error=str(e)
            )
            return {"paths": [], "recommended": None}

    async def _discover_appdata_paths(self, host: DockerHost) -> dict[str, Any]:
        """Discover appdata/volume storage locations from container bind mounts."""
        try:
            # Use cache manager if available, otherwise fall back to SSH
            if self.cache_manager:
                return await self._discover_appdata_paths_cached(host)
            else:
                return await self._discover_appdata_paths_ssh(host)
        except Exception as e:
            self.logger.error("Appdata path discovery failed", host_id=host.hostname, error=str(e))
            return {"paths": [], "recommended": None, "error": str(e)}

    async def _discover_appdata_paths_cached(self, host: DockerHost) -> dict[str, Any]:
        """Discover appdata paths using cache manager."""
        try:
            # Find host_id from hostname
            host_id = None
            for hid, hconfig in self.config.hosts.items():
                if hconfig.hostname == host.hostname:
                    host_id = hid
                    break

            if not host_id:
                self.logger.warning("Could not find host_id for hostname", hostname=host.hostname)
                return {"paths": [], "recommended": None}

            # Get containers from cache
            containers = await self.cache_manager.get_containers(host_id)

            # Extract bind mount paths from all containers
            all_bind_mounts = []
            for container in containers:
                all_bind_mounts.extend(container.bind_mounts)

            if all_bind_mounts:
                # Find common base paths by analyzing mount points
                base_path_counts = Counter()
                for mount_path in all_bind_mounts:
                    # Skip system paths and temporary mounts
                    if mount_path.startswith(("/proc", "/sys", "/dev", "/tmp", "/var/run")):  # noqa: S108
                        continue

                    # Find potential base appdata paths
                    path_parts = Path(mount_path).parts
                    for i in range(2, min(5, len(path_parts))):  # Check 2-4 levels deep
                        potential_base = str(Path(*path_parts[:i]))
                        if potential_base not in ["/", "/home", "/opt", "/srv", "/mnt"]:
                            base_path_counts[potential_base] += 1

                if base_path_counts:
                    # Get unique paths and recommend the most common one
                    unique_paths = list(base_path_counts.keys())
                    recommended = (
                        base_path_counts.most_common(1)[0][0] if base_path_counts else None
                    )

                    self.logger.info(
                        "Discovered appdata paths from cache",
                        host_id=host_id,
                        paths_found=len(unique_paths),
                        containers_checked=len(containers),
                        bind_mounts_analyzed=len(all_bind_mounts),
                        recommended=recommended,
                    )

                    return {"paths": unique_paths, "recommended": recommended}

            # No bind mounts found
            self.logger.debug("No bind mounts found in cache", host_id=host_id)
            return {"paths": [], "recommended": None}

        except Exception as e:
            self.logger.error("Cache-based appdata discovery failed", error=str(e))
            # Fall back to SSH method
            return await self._discover_appdata_paths_ssh(host)

    async def _discover_appdata_paths_ssh(self, host: DockerHost) -> dict[str, Any]:
        """Discover appdata paths using SSH (fallback method)."""
        try:
            ssh_cmd = build_ssh_command(host)

            # Get bind mount sources from all containers
            inspect_cmd = ssh_cmd + [
                "docker ps -aq --no-trunc | xargs -r docker inspect --format '{{range .Mounts}}{{if eq .Type \"bind\"}}{{.Source}}{{\"\\n\"}}{{end}}{{end}}' 2>/dev/null | grep -v '^$' | sort | uniq"
            ]

            process = await asyncio.create_subprocess_exec(
                *inspect_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, _ = await process.communicate()

            if process.returncode == 0 and stdout:
                # Extract bind mount paths
                bind_mounts = [m.strip() for m in stdout.decode().strip().split("\n") if m.strip()]

                if bind_mounts:
                    # Find common base paths by analyzing mount points
                    base_path_counts = {}
                    for mount_path in bind_mounts:
                        # Skip system paths and temporary mounts
                        if mount_path.startswith(("/proc", "/sys", "/dev", "/tmp", "/var/run")):  # noqa: S108
                            continue

                        # Find potential base appdata paths
                        path_parts = Path(mount_path).parts
                        for i in range(2, min(5, len(path_parts))):  # Check 2-4 levels deep
                            potential_base = str(Path(*path_parts[:i]))
                            if potential_base not in ["/", "/home", "/opt", "/srv", "/mnt"]:
                                base_path_counts[potential_base] = (
                                    base_path_counts.get(potential_base, 0) + 1
                                )

                    if base_path_counts:
                        # Recommend the path with most mounted volumes
                        recommended = max(base_path_counts.items(), key=lambda x: x[1])[0]
                        return {"paths": list(base_path_counts.keys()), "recommended": recommended}

            # Fallback to checking common appdata locations
            search_paths = [
                "/opt/appdata",
                "/srv/docker",
                "/data",
                "/mnt/appdata",
                "/mnt/docker",
                "/opt/docker-data",
            ]

            existing_paths = []
            for path in search_paths:
                test_cmd = ssh_cmd + [f"test -d {path} && test -w {path} && echo '{path}'"]

                process = await asyncio.create_subprocess_exec(
                    *test_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )

                stdout, _ = await process.communicate()

                if process.returncode == 0 and stdout.strip():
                    existing_paths.append(path)

            recommended = existing_paths[0] if existing_paths else None
            return {"paths": existing_paths, "recommended": recommended}

        except Exception as e:
            self.logger.warning(
                "Failed to discover appdata paths", hostname=host.hostname, error=str(e)
            )
            return {"paths": [], "recommended": None}

    async def _discover_zfs_capability(self, host: DockerHost) -> dict[str, Any]:
        """Discover ZFS capabilities on the host."""
        try:
            ssh_cmd = build_ssh_command(host)

            # Check if ZFS is available
            zfs_cmd = ssh_cmd + ["which zfs >/dev/null 2>&1 && zfs version | head -1"]

            process = await asyncio.create_subprocess_exec(
                *zfs_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            version_stdout, _ = await process.communicate()

            if process.returncode != 0:
                return {"capable": False, "reason": "ZFS not available"}

            # Get ZFS pools
            pools_cmd = ssh_cmd + ["zpool list -H -o name 2>/dev/null || true"]

            process = await asyncio.create_subprocess_exec(
                *pools_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            pools_stdout, _ = await process.communicate()
            pools = [p.strip() for p in pools_stdout.decode().strip().split("\n") if p.strip()]

            # Suggest dataset name
            dataset = None
            if pools:
                # Look for existing appdata datasets or suggest one
                for pool in pools:
                    dataset = f"{pool}/appdata"
                    break

            return {
                "capable": True,
                "version": version_stdout.decode().strip() if version_stdout else "unknown",
                "pools": pools,
                "dataset": dataset,
            }

        except Exception as e:
            return {"capable": False, "reason": f"ZFS check failed: {str(e)}"}

    def _recommend_compose_path(self, paths: list[str]) -> str | None:
        """Recommend the best compose path from discovered options."""
        if not paths:
            return None

        # Prioritize paths (most preferred first)
        priorities = ["/opt/docker", "/opt/compose", "/srv/docker", "/srv/compose"]

        for priority_path in priorities:
            for path in paths:
                if priority_path in path:
                    return path

        # Return first available path
        return paths[0] if paths else None

    async def _test_ssh_connection(
        self, hostname: str, user: str, port: int = 22, identity_file: str | None = None
    ) -> bool:
        """Test SSH connection with raw parameters before adding host to config.

        Args:
            hostname: SSH hostname or IP address
            user: SSH username
            port: SSH port (default: 22)
            identity_file: Path to SSH private key file

        Returns:
            True if SSH connection successful, False otherwise
        """
        try:
            # Build SSH command for connection test (similar to test_connection method)
            ssh_cmd = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "StrictHostKeyChecking=accept-new",
            ]

            if port != 22:
                ssh_cmd.extend(["-p", str(port)])

            if identity_file:
                ssh_cmd.extend(["-i", identity_file])

            ssh_cmd.append(f"{user}@{hostname}")
            ssh_cmd.append("echo 'connection_test_ok'")

            # Execute SSH test
            process = await asyncio.create_subprocess_exec(
                *ssh_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()
            output = stdout.decode().strip()

            # Check if connection was successful
            success = process.returncode == 0 and "connection_test_ok" in output

            if success:
                self.logger.info(
                    "SSH connection test successful", hostname=hostname, user=user, port=port
                )
            else:
                self.logger.warning(
                    "SSH connection test failed",
                    hostname=hostname,
                    user=user,
                    port=port,
                    returncode=process.returncode,
                    stderr=stderr.decode().strip()[:200],
                )

            return success

        except Exception as e:
            self.logger.error(
                "SSH connection test exception",
                hostname=hostname,
                user=user,
                port=port,
                error=str(e),
            )
            return False
