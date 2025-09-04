"""
Host Management Service

Business logic for Docker host management operations.
"""

import asyncio
from pathlib import Path
from typing import Any

import structlog

from ..constants import APPDATA_PATH, COMPOSE_PATH, DOCKER_COMPOSE_WORKING_DIR, HOST_ID
from ..core.config_loader import DockerHost, DockerMCPConfig, load_config, save_config
from ..utils import build_ssh_command


class HostService:
    """Service for Docker host management operations."""

    def __init__(self, config: DockerMCPConfig, context_manager=None, cache_manager=None):
        self.config = config
        self.context_manager = context_manager
        self.logger = structlog.get_logger()


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
                        "id": host_id,  # Backward-compatible alias used by some tests/tools
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

            # Create human-readable summary for efficient display
            summary_lines = [
                f"Docker Hosts ({len(hosts)} configured)",
                f"{'Host':<12} {'Address':<20} {'ZFS':<3} {'Dataset':<20}",
                f"{'-'*12:<12} {'-'*20:<20} {'-'*3:<3} {'-'*20:<20}",
            ]
            
            for host_data in hosts:
                zfs_indicator = "✓" if host_data.get('zfs_capable') else "✗"
                address = f"{host_data['hostname']}:{host_data['port']}"
                dataset = host_data.get('zfs_dataset', '-') or '-'
                
                summary_lines.append(
                    f"{host_data[HOST_ID]:<12} {address:<20} {zfs_indicator:<3} {dataset[:20]:<20}"
                )
            
            return {
                "success": True, 
                "hosts": hosts, 
                "count": len(hosts),
                "summary": "\n".join(summary_lines)
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
        zfs_capable: bool | None = None,
        zfs_dataset: str | None = None,
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
            zfs_capable: Whether ZFS is available on the host (optional update)
            zfs_dataset: ZFS dataset path for appdata (optional update)
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
            updated_config = {
                "hostname": ssh_host if ssh_host is not None and ssh_host != "" else current_host.hostname,
                "user": ssh_user if ssh_user is not None and ssh_user != "" else current_host.user,
                "port": ssh_port if ssh_port is not None else current_host.port,
                "identity_file": ssh_key_path
                if ssh_key_path is not None and ssh_key_path != ""
                else current_host.identity_file,
                "description": description if description is not None and description != "" else current_host.description,
                "tags": tags if tags is not None else current_host.tags,
                COMPOSE_PATH: compose_path
                if compose_path is not None and compose_path != ""
                else current_host.compose_path,
                APPDATA_PATH: appdata_path
                if appdata_path is not None and appdata_path != ""
                else current_host.appdata_path,
                "zfs_capable": zfs_capable if zfs_capable is not None else current_host.zfs_capable,
                "zfs_dataset": zfs_dataset
                if zfs_dataset is not None and zfs_dataset != ""
                else current_host.zfs_dataset,
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
                save_config(self.config, getattr(self.config, "config_file", None))
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
                        ("ssh_", "description", "tags", "compose_", "appdata_", "zfs_", "enabled")
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
            # Force reload configuration from disk to avoid stale in-memory state
            try:
                config_file_path = getattr(self.config, "config_file", None)
                fresh_config = load_config(config_file_path)
                self.config = fresh_config
                self.logger.info(
                    "Reloaded configuration from disk before discovery",
                    host_id=host_id,
                    config_file_path=config_file_path
                )
            except Exception as reload_error:
                self.logger.warning(
                    "Failed to reload config from disk, using in-memory config",
                    host_id=host_id,
                    error=str(reload_error)
                )
            
            # Check if host exists
            if host_id not in self.config.hosts:
                return {"success": False, "error": f"Host '{host_id}' not found", HOST_ID: host_id}

            host = self.config.hosts[host_id]

            # Run discovery operations in parallel with individual timeout
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(
                        self._discover_compose_paths(host),
                        self._discover_appdata_paths(host),
                        self._discover_zfs_capability(host),
                        return_exceptions=True,
                    ),
                    timeout=30.0,  # 30 second timeout per host
                )
            except asyncio.TimeoutError:
                self.logger.warning(f"Discovery timed out for host {host_id}")
                return {
                    "success": False,
                    "error": "Discovery timed out after 30 seconds",
                    HOST_ID: host_id,
                }

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
                tag_added = False
                config_changed = False
                
                if "zfs" not in host.tags:
                    host.tags.append("zfs")
                    tag_added = True
                    config_changed = True
                    self.logger.info("Auto-added 'zfs' tag to host", host_id=host_id)

                # Always update ZFS capabilities and dataset if discovered (even if tag existed)
                if not host.zfs_capable:
                    host.zfs_capable = True
                    config_changed = True
                    self.logger.info("Updated zfs_capable to True", host_id=host_id)
                
                discovered_dataset = zfs_result.get("dataset")
                current_dataset = host.zfs_dataset
                
                self.logger.info(
                    "Comparing ZFS datasets",
                    host_id=host_id,
                    current_dataset=current_dataset,
                    current_dataset_type=type(current_dataset).__name__,
                    discovered_dataset=discovered_dataset,
                    discovered_dataset_type=type(discovered_dataset).__name__,
                    datasets_equal=(current_dataset == discovered_dataset)
                )
                
                if discovered_dataset and current_dataset != discovered_dataset:
                    old_dataset = current_dataset
                    host.zfs_dataset = discovered_dataset
                    config_changed = True
                    self.logger.info(
                        "Updated zfs_dataset", 
                        host_id=host_id, 
                        old_dataset=old_dataset, 
                        new_dataset=host.zfs_dataset
                    )

                # Save configuration if any changes were made
                save_success = True
                save_error = None
                if config_changed:
                    try:
                        config_file_path = getattr(self.config, "config_file", None)
                        self.logger.info(
                            "Attempting to save config after ZFS updates",
                            host_id=host_id,
                            config_file_path=config_file_path,
                            zfs_dataset=host.zfs_dataset
                        )
                        save_config(self.config, config_file_path)
                        self.logger.info(
                            "Successfully saved config after ZFS updates",
                            host_id=host_id,
                            config_file_path=config_file_path
                        )
                    except Exception as e:
                        save_success = False
                        save_error = str(e)
                        self.logger.error(
                            "Failed to save config after ZFS updates",
                            host_id=host_id,
                            config_file_path=getattr(self.config, "config_file", None),
                            error=str(e),
                        )

                zfs_recommendation = {
                    "type": "zfs_config",
                    "message": "ZFS support detected and 'zfs' tag automatically added"
                    if tag_added
                    else "ZFS support detected ('zfs' tag already present)",
                    "zfs_dataset": zfs_result.get("dataset"),
                    "tag_added": tag_added,
                }
                
                # Add save status if config was changed
                if config_changed:
                    zfs_recommendation["config_saved"] = save_success
                    if not save_success:
                        zfs_recommendation["save_error"] = save_error
                        zfs_recommendation["message"] += f" (WARNING: Config save failed: {save_error})"
                        
                capabilities["recommendations"].append(zfs_recommendation)

            # Add overall guidance if discovery found nothing useful
            total_paths_found = len(compose_result["paths"]) + len(appdata_result["paths"])
            has_useful_discovery = (
                total_paths_found > 0
                or zfs_result["capable"]
                or len(capabilities["recommendations"]) > 0
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

            self.logger.info(
                "Host capabilities discovered",
                host_id=host_id,
                compose_paths_found=len(compose_result["paths"]),
                appdata_paths_found=len(appdata_result["paths"]),
                zfs_capable=zfs_result["capable"],
                has_useful_discovery=has_useful_discovery,
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

            # Run all discoveries in parallel with timeout
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*discovery_tasks, return_exceptions=True),
                    timeout=60.0,  # 60 second timeout for all discoveries
                )
            except asyncio.TimeoutError:
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
            self.logger.info("Starting sequential discovery for all hosts", total_hosts=len(self.config.hosts))

            discoveries = {}
            successful_discoveries = 0
            failed_discoveries = 0
            enabled_hosts = []

            # Collect enabled hosts first
            for host_id, host_config in self.config.hosts.items():
                if host_config.enabled:
                    enabled_hosts.append(host_id)

            if not enabled_hosts:
                return {
                    "success": True,
                    "action": "discover_all",
                    "total_hosts": 0,
                    "successful_discoveries": 0,
                    "failed_discoveries": 0,
                    "discoveries": {},
                    "summary": "No enabled hosts to discover",
                }

            # Process each host one at a time
            for i, host_id in enumerate(enabled_hosts, 1):
                self.logger.info(
                    f"Starting discovery for host {host_id} ({i}/{len(enabled_hosts)})"
                )

                try:
                    # Set a reasonable timeout for single host discovery
                    result = await asyncio.wait_for(
                        self.discover_host_capabilities(host_id),
                        timeout=30.0  # 30 seconds per host
                    )

                    discoveries[host_id] = result
                    if result.get("success"):
                        successful_discoveries += 1
                        self.logger.info(f"Discovery completed successfully for host {host_id}")
                    else:
                        failed_discoveries += 1
                        self.logger.warning(f"Discovery failed for host {host_id}: {result.get('error', 'Unknown error')}")

                except asyncio.TimeoutError:
                    error_msg = "Discovery timed out after 30 seconds"
                    self.logger.error(f"Discovery timed out for host {host_id}")
                    discoveries[host_id] = {
                        "success": False,
                        "error": error_msg,
                        "host_id": host_id
                    }
                    failed_discoveries += 1

                except Exception as e:
                    error_msg = str(e)
                    self.logger.error(f"Discovery failed for host {host_id}: {error_msg}")
                    discoveries[host_id] = {
                        "success": False,
                        "error": error_msg,
                        "host_id": host_id
                    }
                    failed_discoveries += 1

            # Calculate summary statistics
            total_recommendations = 0
            zfs_capable_hosts = 0
            total_paths_found = 0

            for discovery in discoveries.values():
                if discovery.get("success") and discovery.get("recommendations"):
                    total_recommendations += len(discovery["recommendations"])
                if discovery.get("zfs_discovery", {}).get("capable"):
                    zfs_capable_hosts += 1
                if discovery.get("compose_discovery", {}).get("paths"):
                    total_paths_found += len(discovery["compose_discovery"]["paths"])
                if discovery.get("appdata_discovery", {}).get("paths"):
                    total_paths_found += len(discovery["appdata_discovery"]["paths"])

            # Return comprehensive results
            return {
                "success": True,
                "action": "discover_all",
                "total_hosts": len(enabled_hosts),
                "successful_discoveries": successful_discoveries,
                "failed_discoveries": failed_discoveries,
                "discoveries": discoveries,
                "summary": f"Discovered {successful_discoveries}/{len(enabled_hosts)} hosts successfully",
                "discovery_summary": {
                    "total_hosts_discovered": len(discoveries),
                    "total_recommendations": total_recommendations,
                    "zfs_capable_hosts": zfs_capable_hosts,
                    "total_paths_found": total_paths_found,
                }
            }

        except Exception as e:
            self.logger.error("Sequential discovery failed", error=str(e))
            return {
                "success": False,
                "error": f"Sequential discovery failed: {str(e)}",
                "action": "discover_all",
            }

    async def _discover_compose_paths(self, host: DockerHost) -> dict[str, Any]:
        """Discover Docker Compose file locations from running containers."""
        try:
            # Use SSH for compose path discovery
            return await self._discover_compose_paths_ssh(host)
        except Exception as e:
            self.logger.error("Compose path discovery failed", host_id=host.hostname, error=str(e))
            return {"paths": [], "recommended": None, "error": str(e)}


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
            # Use SSH for appdata path discovery
            return await self._discover_appdata_paths_ssh(host)
        except Exception as e:
            self.logger.error("Appdata path discovery failed", host_id=host.hostname, error=str(e))
            return {"paths": [], "recommended": None, "error": str(e)}


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

            # Suggest dataset name with intelligent pool selection
            dataset = None
            if pools:
                # System pools to avoid for appdata storage
                system_pools = ['bpool', 'boot-pool', 'boot']

                # First, check for existing appdata datasets on any pool
                for pool in pools:
                    if pool not in system_pools:
                        # Check if appdata dataset already exists on this pool
                        check_existing_cmd = ssh_cmd + [
                            f"zfs list {pool}/appdata >/dev/null 2>&1 && echo 'EXISTS' || echo 'NOT_FOUND'"
                        ]
                        try:
                            process = await asyncio.create_subprocess_exec(
                                *check_existing_cmd,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE
                            )
                            stdout, _ = await process.communicate()
                            if process.returncode == 0 and "EXISTS" in stdout.decode():
                                dataset = f"{pool}/appdata"
                                break
                        except Exception:
                            # Continue if check fails
                            pass

                # If no existing appdata dataset found, use intelligent pool selection
                if not dataset:
                    # Prefer 'rpool' if available and not a system pool
                    if 'rpool' in pools and 'rpool' not in system_pools:
                        dataset = 'rpool/appdata'
                    else:
                        # Use first non-system pool
                        for pool in pools:
                            if pool not in system_pools:
                                dataset = f"{pool}/appdata"
                                break

                    # Last resort: use first available pool even if it's a system pool
                    if not dataset and pools:
                        dataset = f"{pools[0]}/appdata"

            return {
                "capable": True,
                "version": version_stdout.decode().strip() if version_stdout else "unknown",
                "pools": pools,
                "dataset": dataset,
            }

        except Exception as e:
            return {"capable": False, "reason": f"ZFS check failed: {str(e)}"}

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
            # Import dependencies for this handler
            from ..models.enums import HostAction

            # Route to appropriate handler with validation
            if action == HostAction.LIST:
                result = await self.list_docker_hosts()
                # Convert dict with summary to ToolResult for proper formatting
                if isinstance(result, dict) and "summary" in result:
                    from fastmcp.tools.tool import ToolResult
                    from mcp.types import TextContent
                    return ToolResult(
                        content=[TextContent(type="text", text=result["summary"])],
                        structured_content=result
                    )
                return result

            elif action == HostAction.ADD:
                # Extract parameters
                host_id = params.get("host_id", "")
                ssh_host = params.get("ssh_host", "")
                ssh_user = params.get("ssh_user", "")
                ssh_port = params.get("ssh_port", 22)
                ssh_key_path = params.get("ssh_key_path")
                description = params.get("description", "")
                tags = params.get("tags", [])
                compose_path = params.get("compose_path")
                enabled = params.get("enabled", True)

                # Validate required parameters for add action
                if not host_id:
                    return {"success": False, "error": "host_id is required for add action"}
                if not ssh_host:
                    return {"success": False, "error": "ssh_host is required for add action"}
                if not ssh_user:
                    return {"success": False, "error": "ssh_user is required for add action"}

                # Validate port range
                if not (1 <= ssh_port <= 65535):
                    return {
                        "success": False,
                        "error": f"ssh_port must be between 1 and 65535, got {ssh_port}",
                    }

                # Add host with auto-discovery
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

                # Auto-run discovery if host was added successfully (always enabled)
                if result.get("success"):
                    discovery_result = await self.discover_host_capabilities(host_id)
                    if discovery_result.get("success") and discovery_result.get("recommendations"):
                        result["discovery"] = discovery_result
                        result["message"] += " (Discovery completed - check recommendations)"

                return result

            elif action == HostAction.EDIT:
                # Extract parameters
                host_id = params.get("host_id", "")
                ssh_host = params.get("ssh_host")
                ssh_user = params.get("ssh_user")
                ssh_port = params.get("ssh_port")
                ssh_key_path = params.get("ssh_key_path")
                description = params.get("description")
                tags = params.get("tags")
                compose_path = params.get("compose_path")
                appdata_path = params.get("appdata_path")
                zfs_capable = params.get("zfs_capable")
                zfs_dataset = params.get("zfs_dataset")
                enabled = params.get("enabled")

                # Validate required parameters for edit action
                if not host_id:
                    return {"success": False, "error": "host_id is required for edit action"}

                return await self.edit_docker_host(
                    host_id,
                    ssh_host,
                    ssh_user,
                    ssh_port,
                    ssh_key_path,
                    description,
                    tags,
                    compose_path,
                    appdata_path,
                    zfs_capable,
                    zfs_dataset,
                    enabled,
                )

            elif action == HostAction.REMOVE:
                # Extract parameters
                host_id = params.get("host_id", "")

                # Validate required parameters for remove action
                if not host_id:
                    return {"success": False, "error": "host_id is required for remove action"}

                return await self.remove_docker_host(host_id)

            elif action == HostAction.TEST_CONNECTION:
                # Extract parameters
                host_id = params.get("host_id", "")

                # Validate required parameters for test_connection action
                if not host_id:
                    return {
                        "success": False,
                        "error": "host_id is required for test_connection action",
                    }

                return await self.test_connection(host_id)

            elif action == HostAction.DISCOVER:
                # Extract parameters
                host_id = params.get("host_id", "")

                # Support both 'all' and specific host_id
                if host_id == "all" or not host_id:
                    # Sequential discovery for all hosts (prevents timeouts)
                    result = await self.discover_all_hosts_sequential()
                    return self._format_discover_all_result(result)
                else:
                    # Single host discovery (fast)
                    result = await self.discover_host_capabilities(host_id)
                    return self._format_discover_result(result, host_id)

            elif action == HostAction.PORTS:
                # Import container service dependency
                from ..services import ContainerService

                container_service = ContainerService(self.config, self.context_manager)

                # Extract parameters
                host_id = params.get("host_id", "")
                port = params.get("port", 0)

                # Validate required parameters for ports action
                if not host_id:
                    return {"success": False, "error": "host_id is required for ports action"}

                # Handle sub-actions: "list" (default) or "check"
                # For ports check, port parameter must be provided
                if port > 0:
                    # Check specific port availability
                    return await container_service.check_port_availability(host_id, port)
                else:
                    # List all ports (simplified - always include stopped containers)
                    result = await container_service.list_host_ports(host_id)
                    # Return the full ToolResult to preserve formatting
                    return result

            elif action == HostAction.IMPORT_SSH:
                # Import config service dependency
                from ..services import ConfigService

                config_service = ConfigService(
                    self.config, None
                )  # Context manager not needed for config

                # Extract parameters
                ssh_config_path = params.get("ssh_config_path")
                selected_hosts = params.get("selected_hosts")

                result = await config_service.import_ssh_config(ssh_config_path, selected_hosts)
                # Extract structured content for processing but preserve original ToolResult
                if hasattr(result, "structured_content"):
                    import_result = result.structured_content or {
                        "success": True,
                        "data": str(result.content),
                    }
                else:
                    import_result = result

                # Auto-run discovery on imported hosts if import was successful
                if import_result.get("success") and import_result.get("imported_hosts"):
                    discovered_hosts = []
                    for host_info in import_result["imported_hosts"]:
                        host_id = host_info["host_id"]

                        # Run test_connection and discover for each imported host
                        try:
                            test_result = await self.test_connection(host_id)
                            discovery_result = await self.discover_host_capabilities(host_id)

                            discovered_hosts.append(
                                {
                                    "host_id": host_id,
                                    "connection_test": test_result.get("success", False),
                                    "discovery": discovery_result.get("success", False),
                                    "recommendations": discovery_result.get("recommendations", []),
                                }
                            )
                        except Exception as e:
                            self.logger.error(
                                "Auto-discovery failed for imported host",
                                host_id=host_id,
                                error=str(e),
                            )
                            discovered_hosts.append(
                                {
                                    "host_id": host_id,
                                    "connection_test": False,
                                    "discovery": False,
                                    "error": str(e),
                                }
                            )

                    # Add discovery results to import result
                    import_result["auto_discovery"] = {
                        "completed": True,
                        "results": discovered_hosts,
                    }
                    import_result["message"] = (
                        import_result.get("message", "")
                        + " (Auto-discovery completed for imported hosts)"
                    )

                return import_result

            elif action == HostAction.CLEANUP:
                # Import cleanup service dependency
                from ..services import CleanupService

                cleanup_service = CleanupService(self.config)

                # Extract parameters
                host_id = params.get("host_id", "")
                cleanup_type = params.get("cleanup_type")
                frequency = params.get("frequency")
                time = params.get("time")

                # Handle cleanup sub-actions:
                # - "cleanup check <host_id>" -> Check disk usage
                # - "cleanup <cleanup_type> <host_id>" -> Execute cleanup
                # - "cleanup schedule" with frequency/time -> Add schedule
                # - "cleanup schedule" without frequency/time -> List or remove schedules

                # Handle schedule operations when frequency is provided (add schedule)
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
                    return await cleanup_service.add_schedule(
                        host_id, cleanup_type, frequency, time
                    )

                # Handle schedule list when no host_id but no frequency (list all schedules)
                elif not host_id and not frequency and not cleanup_type:
                    return await cleanup_service.list_schedules()

                # Handle schedule remove when host_id but no frequency/cleanup_type
                elif host_id and not frequency and not cleanup_type:
                    return await cleanup_service.remove_schedule(host_id)

                # Handle cleanup operations
                else:
                    if not host_id:
                        return {"success": False, "error": "host_id is required for cleanup action"}
                    if not cleanup_type:
                        return {
                            "success": False,
                            "error": "cleanup_type is required for cleanup action",
                        }
                    if cleanup_type not in ["check", "safe", "moderate", "aggressive"]:
                        return {
                            "success": False,
                            "error": "cleanup_type must be one of: check, safe, moderate, aggressive",
                        }

                    if cleanup_type == "check":
                        # Run structured cleanup analysis that explicitly includes type & message
                        return await cleanup_service.docker_cleanup(host_id, "check")
                    else:
                        # Perform actual cleanup
                        return await cleanup_service.docker_cleanup(host_id, cleanup_type)

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
            "host_id": host_id,
            "paths_discovered": discovery_count,
            "zfs_capable": result.get("zfs_discovery", {}).get("capable", False),
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
        zfs_hosts = 0
        total_paths = 0

        discoveries = result.get("discoveries", {})
        for host_discovery in discoveries.values():
            if host_discovery.get("success"):
                total_recommendations += len(host_discovery.get("recommendations", []))
                if host_discovery.get("zfs_discovery", {}).get("capable"):
                    zfs_hosts += 1

                compose_paths = len(host_discovery.get("compose_discovery", {}).get("paths", []))
                appdata_paths = len(host_discovery.get("appdata_discovery", {}).get("paths", []))
                total_paths += compose_paths + appdata_paths

        result["discovery_summary"] = {
            "total_hosts_discovered": result.get("successful_discoveries", 0),
            "total_recommendations": total_recommendations,
            "zfs_capable_hosts": zfs_hosts,
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
