"""
Host Management Service

Business logic for Docker host management operations.
"""

import asyncio
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from docker_mcp.core.docker_context import DockerContextManager
else:
    DockerContextManager = "DockerContextManager"

import structlog

from ..constants import APPDATA_PATH, COMPOSE_PATH, DOCKER_COMPOSE_WORKING_DIR, HOST_ID
from ..core.config_loader import DockerHost, DockerMCPConfig, load_config, save_config
from ..utils import build_ssh_command


class HostService:
    """Service for Docker host management operations."""

    def __init__(
        self,
        config: DockerMCPConfig,
        context_manager: "DockerContextManager | None" = None,
        cache_manager=None,
    ):
        self.config = config
        self.context_manager = context_manager
        self.logger = structlog.get_logger()
        self._config_lock = asyncio.Lock()

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
            await asyncio.to_thread(
                save_config, self.config, getattr(self.config, "config_file", None)
            )

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
                        "id": host_id,  # Backward-compatible alias used by some tests/tools
                        "hostname": host_config.hostname,
                        "user": host_config.user,
                        "port": host_config.port,
                        "description": host_config.description,
                        "tags": host_config.tags,
                        "enabled": host_config.enabled,
                        COMPOSE_PATH: host_config.compose_path,
                        APPDATA_PATH: host_config.appdata_path,
                    }
                )

            # Create human-readable summary for efficient display
            summary_lines = [
                f"Docker Hosts ({len(hosts)} configured)",
                f"{'Host':<12} {'Address':<20}",
                f"{'-' * 12:<12} {'-' * 20:<20}",
            ]

            for host_data in hosts:
                host_data = cast(dict[str, Any], host_data)  # Type hint for mypy
                address = f"{host_data['hostname']}:{host_data['port']}"

                summary_lines.append(
                    f"{host_data.get(HOST_ID, 'unknown'):<12} {address:<20}"
                )

            return {
                "success": True,
                "hosts": hosts,
                "count": len(hosts),
                "summary": "\n".join(summary_lines),
            }

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

            # Update only provided values (treat empty strings as None)
            updated_config: dict[str, Any] = {
                "hostname": ssh_host
                if ssh_host is not None and ssh_host != ""
                else current_host.hostname,
                "user": ssh_user if ssh_user is not None and ssh_user != "" else current_host.user,
                "port": ssh_port if ssh_port is not None else current_host.port,
                "identity_file": ssh_key_path
                if ssh_key_path is not None and ssh_key_path != ""
                else current_host.identity_file,
                "description": description
                if description is not None and description != ""
                else current_host.description,
                "tags": tags if tags is not None else current_host.tags,
                COMPOSE_PATH: compose_path
                if compose_path is not None and compose_path != ""
                else current_host.compose_path,
                APPDATA_PATH: appdata_path
                if appdata_path is not None and appdata_path != ""
                else current_host.appdata_path,
                "enabled": enabled if enabled is not None else current_host.enabled,
            }

            # Create and validate the new host configuration before updating
            try:
                new_host_config = DockerHost(**updated_config)
            except Exception as validation_error:
                return {
                    "success": False,
                    "error": f"Configuration validation failed: {validation_error}",
                    HOST_ID: host_id,
                }

            # Update the in-memory host configuration only after validation succeeds
            self.config.hosts[host_id] = new_host_config

            # Save configuration changes to disk
            try:
                await asyncio.to_thread(
                    save_config, self.config, getattr(self.config, "config_file", None)
                )
            except Exception as save_error:
                # Rollback the in-memory change if save fails
                self.config.hosts[host_id] = current_host
                return {
                    "success": False,
                    "error": f"Failed to save configuration: {save_error}",
                    HOST_ID: host_id,
                }

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
            await asyncio.to_thread(
                save_config, self.config, getattr(self.config, "config_file", None)
            )

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
        """Discover host capabilities including paths.

        Args:
            host_id: Host identifier to discover capabilities for

        Returns:
            Discovery results with recommendations
        """
        try:
            # Force reload configuration from disk to avoid stale in-memory state
            await self._reload_config(host_id)

            # Check if host exists
            if host_id not in self.config.hosts:
                return {"success": False, "error": f"Host '{host_id}' not found", HOST_ID: host_id}

            host = self.config.hosts[host_id]

            # Run discovery operations in parallel with timeout
            discovery_results = await self._run_parallel_discovery(host, host_id)
            if "error" in discovery_results:
                return discovery_results

            # Process discovery results
            compose_result, appdata_result = self._process_discovery_results(
                discovery_results
            )

            # Compile base capabilities
            capabilities = {
                "success": True,
                HOST_ID: host_id,
                "compose_discovery": compose_result,
                "appdata_discovery": appdata_result,
                "recommendations": [],
            }

            # Generate recommendations
            await self._generate_recommendations(
                capabilities, compose_result, appdata_result, host_id
            )

            # Add overall guidance if needed
            self._add_overall_guidance(
                capabilities, compose_result, appdata_result, host_id
            )

            self.logger.info(
                "Host capabilities discovered",
                host_id=host_id,
                compose_paths_found=len(compose_result["paths"]),
                appdata_paths_found=len(appdata_result["paths"]),
            )

            return capabilities

        except Exception as e:
            self.logger.error("Failed to discover host capabilities", host_id=host_id, error=str(e))
            return {
                "success": False,
                "error": f"Discovery failed: {str(e)}",
                HOST_ID: host_id,
            }

    async def _reload_config(self, host_id: str) -> None:
        """Reload configuration from disk to avoid stale in-memory state."""
        try:
            config_file_path = getattr(self.config, "config_file", None)
            fresh_config = await asyncio.to_thread(load_config, config_file_path)
            async with self._config_lock:
                self.config = fresh_config
            self.logger.info(
                "Reloaded configuration from disk before discovery",
                host_id=host_id,
                config_file_path=config_file_path,
            )
        except Exception as reload_error:
            self.logger.warning(
                "Failed to reload config from disk, using in-memory config",
                host_id=host_id,
                error=str(reload_error),
            )

    async def _run_parallel_discovery(self, host, host_id: str) -> dict[str, Any]:
        """Run discovery operations in parallel with timeout."""
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    self._discover_compose_paths(host),
                    self._discover_appdata_paths(host),
                    return_exceptions=True,
                ),
                timeout=30.0,  # 30 second timeout per host
            )
            return {"results": results}
        except TimeoutError:
            self.logger.warning(f"Discovery timed out for host {host_id}")
            return {
                "success": False,
                "error": "Discovery timed out after 30 seconds",
                HOST_ID: host_id,
            }

    def _process_discovery_results(
        self, discovery_data: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Process discovery results into structured data."""
        results = discovery_data["results"]

        compose_result: dict[str, Any] = (
            {**results[0], "success": True}
            if not isinstance(results[0], Exception)
            else {"paths": [], "recommended": None, "success": False}
        )
        appdata_result: dict[str, Any] = (
            {**results[1], "success": True}
            if not isinstance(results[1], Exception)
            else {"paths": [], "recommended": None, "success": False}
        )

        return compose_result, appdata_result

    async def _generate_recommendations(
        self,
        capabilities: dict[str, Any],
        compose_result: dict[str, Any],
        appdata_result: dict[str, Any],
        host_id: str,
    ) -> None:
        """Generate configuration recommendations."""
        # Add compose path recommendation
        if compose_result["recommended"]:
            capabilities["recommendations"].append(
                {
                    "type": COMPOSE_PATH,
                    "message": f"Set compose_path to '{compose_result['recommended']}'",
                    "value": compose_result["recommended"],
                }
            )

        # Add appdata path recommendation
        if appdata_result["recommended"]:
            capabilities["recommendations"].append(
                {
                    "type": APPDATA_PATH,
                    "message": f"Set appdata_path to '{appdata_result['recommended']}'",
                    "value": appdata_result["recommended"],
                }
            )




    def _add_overall_guidance(
        self,
        capabilities: dict[str, Any],
        compose_result: dict[str, Any],
        appdata_result: dict[str, Any],
        host_id: str,
    ) -> None:
        """Add overall guidance if discovery found nothing useful."""
        total_paths_found = len(cast(list, compose_result["paths"])) + len(
            cast(list, appdata_result["paths"])
        )
        has_useful_discovery = (
            total_paths_found > 0
            or len(cast(list, capabilities["recommendations"])) > 0
        )

        if not has_useful_discovery:
            capabilities["overall_guidance"] = (
                "Discovery found no automatic configuration. This is common for:\n"
                "• New hosts with no containers yet\n"
                "• Hosts using Docker volumes instead of bind mounts\n"
                "• Custom deployment methods\n\n"
                "Next steps:\n"
                f"1. Deploy containers: docker_compose deploy {host_id} <stack_name>\n"
                f"2. Manually configure paths: docker_hosts edit {host_id} compose_path /path appdata_path /path\n"
                "3. Check the guidance in compose_discovery and appdata_discovery for specific suggestions"
            )

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

            # Run all discoveries in parallel with timeout
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*discovery_tasks, return_exceptions=True),
                    timeout=60.0,  # 60 second timeout for all discoveries
                )
            except TimeoutError:
                self.logger.warning("Discovery operation timed out after 60 seconds")
                # Return partial results - what we have so far
                results = [Exception("Discovery timed out") for _ in host_ids]

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
                    result = cast(dict[str, Any], result)
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

    async def discover_all_hosts_sequential(self) -> dict[str, Any]:
        """Discover capabilities for all hosts SEQUENTIALLY to avoid timeouts.

        Processes hosts one at a time to prevent SSH channel exhaustion
        and avoid parallel processing overload that causes timeouts.

        Returns:
            Discovery results for all hosts with summary
        """
        try:
            self.logger.info(
                "Starting sequential discovery for all hosts", total_hosts=len(self.config.hosts)
            )

            # Collect enabled hosts first
            enabled_hosts = self._collect_enabled_hosts()
            if not enabled_hosts:
                return self._create_empty_discovery_result()

            # Process each host sequentially
            (
                discoveries,
                successful_discoveries,
                failed_discoveries,
            ) = await self._process_hosts_sequentially(enabled_hosts)

            # Calculate summary statistics and return results
            discovery_stats = self._calculate_discovery_statistics(discoveries)
            return self._create_discovery_summary(
                enabled_hosts,
                successful_discoveries,
                failed_discoveries,
                discoveries,
                discovery_stats,
            )

        except Exception as e:
            self.logger.error("Sequential discovery failed", error=str(e))
            return {
                "success": False,
                "error": f"Sequential discovery failed: {str(e)}",
                "action": "discover_all",
            }

    def _collect_enabled_hosts(self) -> list[str]:
        """Collect list of enabled hosts."""
        enabled_hosts = []
        for host_id, host_config in self.config.hosts.items():
            if host_config.enabled:
                enabled_hosts.append(host_id)
        return enabled_hosts

    def _create_empty_discovery_result(self) -> dict[str, Any]:
        """Create result for when no enabled hosts are found."""
        return {
            "success": True,
            "action": "discover_all",
            "total_hosts": 0,
            "successful_discoveries": 0,
            "failed_discoveries": 0,
            "discoveries": {},
            "summary": "No enabled hosts to discover",
        }

    async def _process_hosts_sequentially(
        self, enabled_hosts: list[str]
    ) -> tuple[dict[str, Any], int, int]:
        """Process each host discovery sequentially."""
        discoveries = {}
        successful_discoveries = 0
        failed_discoveries = 0

        for i, host_id in enumerate(enabled_hosts, 1):
            self.logger.info(f"Starting discovery for host {host_id} ({i}/{len(enabled_hosts)})")

            result, success = await self._process_single_host_discovery(host_id)
            discoveries[host_id] = result

            if success:
                successful_discoveries += 1
                self.logger.info(f"Discovery completed successfully for host {host_id}")
            else:
                failed_discoveries += 1
                self.logger.warning(
                    f"Discovery failed for host {host_id}: {result.get('error', 'Unknown error')}"
                )

        return discoveries, successful_discoveries, failed_discoveries

    async def _process_single_host_discovery(self, host_id: str) -> tuple[dict[str, Any], bool]:
        """Process discovery for a single host with error handling."""
        try:
            result = await asyncio.wait_for(
                self.discover_host_capabilities(host_id),
                timeout=30.0,  # 30 seconds per host
            )
            return result, result.get("success", False)

        except TimeoutError:
            error_msg = "Discovery timed out after 30 seconds"
            self.logger.error(f"Discovery timed out for host {host_id}")
            return {"success": False, "error": error_msg, HOST_ID: host_id}, False

        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"Discovery failed for host {host_id}: {error_msg}")
            return {"success": False, "error": error_msg, HOST_ID: host_id}, False

    def _calculate_discovery_statistics(self, discoveries: dict[str, Any]) -> dict[str, int]:
        """Calculate summary statistics from discovery results."""
        total_recommendations = 0
        total_paths_found = 0

        for discovery in discoveries.values():
            if discovery.get("success") and discovery.get("recommendations"):
                total_recommendations += len(discovery["recommendations"])
            if discovery.get("compose_discovery", {}).get("paths"):
                total_paths_found += len(discovery["compose_discovery"]["paths"])
            if discovery.get("appdata_discovery", {}).get("paths"):
                total_paths_found += len(discovery["appdata_discovery"]["paths"])

        return {
            "total_recommendations": total_recommendations,
            "total_paths_found": total_paths_found,
        }

    def _create_discovery_summary(
        self,
        enabled_hosts: list[str],
        successful: int,
        failed: int,
        discoveries: dict[str, Any],
        stats: dict[str, int],
    ) -> dict[str, Any]:
        """Create comprehensive discovery results summary."""
        return {
            "success": True,
            "action": "discover_all",
            "total_hosts": len(enabled_hosts),
            "successful_discoveries": successful,
            "failed_discoveries": failed,
            "discoveries": discoveries,
            "summary": f"Discovered {successful}/{len(enabled_hosts)} hosts successfully",
            "discovery_summary": {"total_hosts_discovered": len(discoveries), **stats},
        }

    async def _discover_compose_paths(self, host: DockerHost) -> dict[str, Any]:
        """Discover Docker Compose file locations from running containers."""
        try:
            # Use SSH for compose path discovery
            return await self._discover_compose_paths_ssh(host)
        except Exception as e:
            self.logger.error("Compose path discovery failed", host_id=host.hostname, error=str(e))
            return {"success": False, "paths": [], "recommended": None, "error": str(e)}

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

                    return {
                        "success": True,
                        "paths": list(path_counts.keys()),
                        "recommended": recommended,
                    }

            # Fallback to file system search if no running containers with compose labels
            fallback_cmd = ssh_cmd + [
                "find /opt /srv /home /mnt -maxdepth 3 \\( -name 'docker-compose.*' -o -name 'compose.*' \\) 2>/dev/null | head -10"
            ]

            process = await asyncio.create_subprocess_exec(
                *fallback_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, _ = await process.communicate()

            if process.returncode == 0 and stdout:
                compose_files = stdout.decode().strip().split("\n")
                directories = list(set(str(Path(f).parent) for f in compose_files if f.strip()))
                recommended = self._recommend_compose_path(directories)
                return {"success": True, "paths": directories, "recommended": recommended}

            return {"success": True, "paths": [], "recommended": None}

        except Exception as e:
            self.logger.warning(
                "Failed to discover compose paths", hostname=host.hostname, error=str(e)
            )
            return {"success": True, "paths": [], "recommended": None}

    async def _discover_appdata_paths(self, host: DockerHost) -> dict[str, Any]:
        """Discover appdata/volume storage locations from container bind mounts."""
        try:
            # Use SSH for appdata path discovery
            return await self._discover_appdata_paths_ssh(host)
        except Exception as e:
            self.logger.error("Appdata path discovery failed", host_id=host.hostname, error=str(e))
            return {"success": False, "paths": [], "recommended": None, "error": str(e)}

    async def _discover_appdata_paths_ssh(self, host: DockerHost) -> dict[str, Any]:
        """Discover appdata paths using SSH (fallback method)."""
        try:
            ssh_cmd = build_ssh_command(host)

            # First try to discover from container bind mounts
            result = await self._discover_from_bind_mounts(ssh_cmd)
            if result:
                return result

            # Fallback to checking common appdata locations
            return await self._discover_from_common_paths(ssh_cmd)

        except Exception as e:
            self.logger.warning(
                "Failed to discover appdata paths", hostname=host.hostname, error=str(e)
            )
            return {"success": True, "paths": [], "recommended": None}

    async def _discover_from_bind_mounts(self, ssh_cmd: list[str]) -> dict[str, Any] | None:
        """Discover appdata paths by analyzing container bind mounts."""
        inspect_cmd = ssh_cmd + [
            "docker ps -aq --no-trunc | xargs -r docker inspect --format '{{range .Mounts}}{{if eq .Type \"bind\"}}{{.Source}}{{\"\\n\"}}{{end}}{{end}}' 2>/dev/null | grep -v '^$' | sort | uniq"
        ]

        process = await asyncio.create_subprocess_exec(
            *inspect_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, _ = await process.communicate()

        if process.returncode == 0 and stdout:
            bind_mounts = [m.strip() for m in stdout.decode().strip().split("\n") if m.strip()]

            if bind_mounts:
                base_path_counts = self._analyze_bind_mount_paths(bind_mounts)

                if base_path_counts:
                    # Recommend the path with most mounted volumes
                    recommended = max(base_path_counts.items(), key=lambda x: x[1])[0]
                    return {
                        "success": True,
                        "paths": list(base_path_counts.keys()),
                        "recommended": recommended,
                    }

        return None

    def _analyze_bind_mount_paths(self, bind_mounts: list[str]) -> dict[str, int]:
        """Analyze bind mount paths to find common base paths."""
        base_path_counts = {}

        for mount_path in bind_mounts:
            # Skip system paths and temporary mounts
            if mount_path.startswith(("/proc", "/sys", "/dev", "/tmp", "/var/run")):
                continue

            # Find potential base appdata paths
            path_parts = Path(mount_path).parts
            for i in range(2, min(5, len(path_parts))):  # Check 2-4 levels deep
                potential_base = str(Path(*path_parts[:i]))
                if potential_base not in ["/", "/home", "/opt", "/srv", "/mnt"]:
                    base_path_counts[potential_base] = base_path_counts.get(potential_base, 0) + 1

        return base_path_counts

    async def _discover_from_common_paths(self, ssh_cmd: list[str]) -> dict[str, Any]:
        """Discover appdata paths by checking common locations."""
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
            if await self._test_path_exists_writable(ssh_cmd, path):
                existing_paths.append(path)

        recommended = existing_paths[0] if existing_paths else None
        return {"success": True, "paths": existing_paths, "recommended": recommended}

    async def _test_path_exists_writable(self, ssh_cmd: list[str], path: str) -> bool:
        """Test if a path exists and is writable."""
        test_cmd = ssh_cmd + [
            f"test -d {shlex.quote(path)} && test -w {shlex.quote(path)} && echo {shlex.quote(path)}"
        ]

        process = await asyncio.create_subprocess_exec(
            *test_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, _ = await process.communicate()

        return process.returncode == 0 and bool(stdout.strip())







    def _recommend_compose_path(self, paths: list[str]) -> str | None:
        """Recommend the best compose path from discovered options using smart detection."""
        if not paths:
            return None

        # If only one path, return it
        if len(paths) == 1:
            return paths[0]

        # Prefer persistent storage paths over system paths
        persistent_storage_paths = []
        system_paths = []

        for path in paths:
            # Persistent storage locations (more reliable for user data)
            if any(path.startswith(prefix) for prefix in ["/mnt/", "/data/", "/srv/"]):
                persistent_storage_paths.append(path)
            # System paths (less preferred for user data)
            elif any(path.startswith(prefix) for prefix in ["/opt/", "/home/"]):
                system_paths.append(path)
            else:
                # Unknown path, treat as persistent storage
                persistent_storage_paths.append(path)

        # Prefer persistent storage paths
        if persistent_storage_paths:
            return persistent_storage_paths[0]

        # Fall back to system paths
        if system_paths:
            return system_paths[0]

        # Final fallback
        return paths[0]

    async def handle_action(self, action, **params) -> dict[str, Any]:
        """Unified action handler for all host operations.

        This method consolidates all dispatcher logic from server.py into the service layer.
        """
        try:
            # Normalize action to HostAction enum when provided as string
            if isinstance(action, str):
                from ..models.enums import HostAction

                try:
                    action = HostAction(action.lower().strip())
                except ValueError:
                    return {
                        "success": False,
                        "error": f"Unknown action: {action}",
                        "valid_actions": [a.value for a in HostAction],
                    }

            # Get action handlers mapping
            handlers = self._get_action_handlers()
            handler = handlers.get(action)

            if handler:
                return await handler(**params)
            else:
                return {
                    "success": False,
                    "error": f"Unknown action: {action}",
                    "valid_actions": [
                        "list",
                        "add",
                        "edit",
                        "remove",
                        "test_connection",
                        "discover",
                        "ports",
                        "import_ssh",
                        "cleanup",
                    ],
                }
        except Exception as e:
            self.logger.error("host service action error", action=action, error=str(e))
            return {"success": False, "error": f"Service action failed: {str(e)}", "action": action}

    def _get_action_handlers(self) -> dict:
        """Get mapping of actions to handler methods."""
        from ..models.enums import HostAction

        return {
            HostAction.LIST: self._handle_list_action,
            HostAction.ADD: self._handle_add_action,
            HostAction.EDIT: self._handle_edit_action,
            HostAction.REMOVE: self._handle_remove_action,
            HostAction.TEST_CONNECTION: self._handle_test_connection_action,
            HostAction.DISCOVER: self._handle_discover_action,
            HostAction.PORTS: self._handle_ports_action,
            HostAction.IMPORT_SSH: self._handle_import_ssh_action,
            HostAction.CLEANUP: self._handle_cleanup_action,
        }

    async def _handle_list_action(self, **params) -> dict[str, Any]:
        """Handle LIST action."""
        result = await self.list_docker_hosts()
        return (
            result
            if isinstance(result, dict)
            else {"success": False, "error": "Invalid result format"}
        )

    async def _handle_add_action(self, **params) -> dict[str, Any]:
        """Handle ADD action."""
        host_id = params.get("host_id", "")
        ssh_host = params.get("ssh_host", "")
        ssh_user = params.get("ssh_user", "")
        ssh_port = params.get("ssh_port", 22)
        ssh_key_path = params.get("ssh_key_path")
        description = params.get("description", "")
        tags = params.get("tags", [])
        compose_path = params.get("compose_path")
        enabled = params.get("enabled", True)

        if not host_id:
            return {"success": False, "error": "host_id is required for add action"}
        if not ssh_host:
            return {"success": False, "error": "ssh_host is required for add action"}
        if not ssh_user:
            return {"success": False, "error": "ssh_user is required for add action"}
        if not (1 <= ssh_port <= 65535):
            return {
                "success": False,
                "error": f"ssh_port must be between 1 and 65535, got {ssh_port}",
            }

        result = await self.add_docker_host(
            host_id,
            ssh_host,
            ssh_user,
            ssh_port,
            ssh_key_path,
            description,
            tags,
            compose_path,
            enabled,
        )

        # Auto-run discovery if host was added successfully
        if result.get("success"):
            discovery_result = await self.discover_host_capabilities(host_id)
            if discovery_result.get("success") and discovery_result.get("recommendations"):
                result["discovery"] = discovery_result
                result["message"] += " (Discovery completed - check recommendations)"

        return result

    async def _handle_edit_action(self, **params) -> dict[str, Any]:
        """Handle EDIT action."""
        host_id = params.get("host_id", "")
        if not host_id:
            return {"success": False, "error": "host_id is required for edit action"}

        return await self.edit_docker_host(
            host_id,
            params.get("ssh_host"),
            params.get("ssh_user"),
            params.get("ssh_port"),
            params.get("ssh_key_path"),
            params.get("description"),
            params.get("tags"),
            params.get("compose_path"),
            params.get("appdata_path"),
            params.get("enabled"),
        )

    async def _handle_remove_action(self, **params) -> dict[str, Any]:
        """Handle REMOVE action."""
        host_id = params.get("host_id", "")
        if not host_id:
            return {"success": False, "error": "host_id is required for remove action"}
        return await self.remove_docker_host(host_id)

    async def _handle_test_connection_action(self, **params) -> dict[str, Any]:
        """Handle TEST_CONNECTION action."""
        host_id = params.get("host_id", "")
        if not host_id:
            return {"success": False, "error": "host_id is required for test_connection action"}
        return await self.test_connection(host_id)

    async def _handle_discover_action(self, **params) -> dict[str, Any]:
        """Handle DISCOVER action."""
        host_id = params.get("host_id", "")

        if host_id == "all" or not host_id:
            result = await self.discover_all_hosts_sequential()
            return self._format_discover_all_result(result)
        else:
            result = await self.discover_host_capabilities(host_id)
            return self._format_discover_result(result, host_id)

    async def _handle_ports_action(self, **params) -> dict[str, Any]:
        """Handle PORTS action."""
        from ..services.container import ContainerService

        host_id = params.get("host_id", "")
        port = params.get("port", 0)

        if not host_id:
            return {"success": False, "error": "host_id is required for ports action"}

        if self.context_manager is None:
            return {"success": False, "error": "Context manager not available"}

        container_service = ContainerService(self.config, self.context_manager)

        if port > 0:
            result = await container_service.check_port_availability(host_id, port)
            return cast(dict[str, Any], result.structured_content)
        else:
            result = await container_service.list_host_ports(host_id)
            return cast(dict[str, Any], result.structured_content)

    async def _handle_import_ssh_action(self, **params) -> dict[str, Any]:
        """Handle IMPORT_SSH action."""
        from ..services import ConfigService

        ssh_config_path = params.get("ssh_config_path")
        selected_hosts = params.get("selected_hosts")

        config_service = ConfigService(self.config, self.context_manager)  # type: ignore[arg-type]
        config_path = getattr(self.config, "config_file", None)
        result = await config_service.import_ssh_config(
            ssh_config_path, selected_hosts, config_path
        )

        if hasattr(result, "structured_content"):
            if result.structured_content:
                import_result = result.structured_content
            else:
                # Missing or falsy structured_content is an error condition
                self.logger.error(
                    "import_ssh_config returned invalid/missing structured_content",
                    ssh_config_path=ssh_config_path,
                    selected_hosts=selected_hosts,
                    result_type=type(result).__name__,
                    has_content=hasattr(result, "content"),
                    content_preview=str(result.content)[:200]
                    if hasattr(result, "content")
                    else None,
                )
                import_result = {
                    "success": False,
                    "error": "import_ssh_config returned invalid/missing structured_content",
                    "detail": f"Expected structured data but received: {type(result.structured_content).__name__}",
                    "operation": "import_ssh_config",
                }
        else:
            import_result = result

        # Auto-run discovery on imported hosts if import was successful
        if import_result.get("success") and import_result.get("imported_hosts"):
            discovered_hosts = []
            for host_info in import_result["imported_hosts"]:
                host_id = host_info[HOST_ID]
                try:
                    test_result = await self.test_connection(host_id)
                    discovery_result = await self.discover_host_capabilities(host_id)
                    discovered_hosts.append(
                        {
                            HOST_ID: host_id,
                            "connection_test": test_result.get("success", False),
                            "discovery": discovery_result.get("success", False),
                            "recommendations": discovery_result.get("recommendations", []),
                        }
                    )
                except Exception as e:
                    self.logger.error(
                        "Auto-discovery failed for imported host", host_id=host_id, error=str(e)
                    )
                    discovered_hosts.append(
                        {
                            HOST_ID: host_id,
                            "connection_test": False,
                            "discovery": False,
                            "error": str(e),
                        }
                    )

            import_result["auto_discovery"] = {"completed": True, "results": discovered_hosts}
            import_result["message"] = (
                import_result.get("message", "") + " (Auto-discovery completed for imported hosts)"
            )

        return import_result

    async def _handle_cleanup_action(self, **params) -> dict[str, Any]:
        """Handle CLEANUP action."""
        from ..services import CleanupService

        host_id = params.get("host_id", "")
        cleanup_type = params.get("cleanup_type")
        frequency = params.get("frequency")
        time = params.get("time")

        cleanup_service = CleanupService(self.config)

        # Handle schedule operations
        if frequency and time:
            if not host_id or not cleanup_type:
                return {
                    "success": False,
                    "error": "host_id and cleanup_type required for scheduling",
                }
            if cleanup_type not in ["safe", "moderate"]:
                return {
                    "success": False,
                    "error": "Only 'safe' and 'moderate' cleanup types can be scheduled",
                }
            return await cleanup_service.add_schedule(host_id, cleanup_type, frequency, time)

        # Handle schedule list/remove
        elif not host_id and not frequency and not cleanup_type:
            return await cleanup_service.list_schedules()
        elif host_id and not frequency and not cleanup_type:
            return await cleanup_service.remove_schedule(host_id)

        # Handle cleanup operations
        else:
            if not host_id:
                return {"success": False, "error": "host_id is required for cleanup action"}
            if not cleanup_type:
                return {"success": False, "error": "cleanup_type is required for cleanup action"}
            if cleanup_type not in ["check", "safe", "moderate", "aggressive"]:
                return {
                    "success": False,
                    "error": "cleanup_type must be one of: check, safe, moderate, aggressive",
                }

            return await cleanup_service.docker_cleanup(host_id, cleanup_type)

    def _format_discover_result(self, result: dict[str, Any], host_id: str) -> dict[str, Any]:
        """Format discovery result for single host."""
        if not result.get("success"):
            return result

        # Add discovery summary information
        discovery_count = 0
        if result.get("compose_discovery", {}).get("paths"):
            discovery_count += len(result["compose_discovery"]["paths"])
        if result.get("appdata_discovery", {}).get("paths"):
            discovery_count += len(result["appdata_discovery"]["paths"])

        result["discovery_summary"] = {
            HOST_ID: host_id,
            "paths_discovered": discovery_count,
            "recommendations_count": len(result.get("recommendations", [])),
        }

        # Collect and format all guidance messages for display
        guidance_messages = []

        if compose_guidance := result.get("compose_discovery", {}).get("guidance"):
            guidance_messages.append(f"📁 **Compose Paths**: {compose_guidance}")

        if appdata_guidance := result.get("appdata_discovery", {}).get("guidance"):
            guidance_messages.append(f"💾 **Appdata Paths**: {appdata_guidance}")

        if overall_guidance := result.get("overall_guidance"):
            guidance_messages.append(f"💡 **Overall Guidance**: {overall_guidance}")

        # Add formatted guidance to result if any guidance exists
        if guidance_messages:
            result["helpful_guidance"] = "\n\n".join(guidance_messages)

        return result

    def _format_discover_all_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Format discovery result for all hosts."""
        if not result.get("success"):
            return result

        # Add summary statistics
        total_recommendations = 0
        total_paths = 0

        discoveries = result.get("discoveries", {})
        for host_discovery in discoveries.values():
            if host_discovery.get("success"):
                total_recommendations += len(host_discovery.get("recommendations", []))

                compose_paths = len(host_discovery.get("compose_discovery", {}).get("paths", []))
                appdata_paths = len(host_discovery.get("appdata_discovery", {}).get("paths", []))
                total_paths += compose_paths + appdata_paths

        result["discovery_summary"] = {
            "total_hosts_discovered": result.get("successful_discoveries", 0),
            "total_recommendations": total_recommendations,
            "total_paths_found": total_paths,
        }

        return result

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
